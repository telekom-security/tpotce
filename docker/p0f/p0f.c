/*
   p0f - main entry point and all the pcap / unix socket innards
   -------------------------------------------------------------

   Copyright (C) 2012 by Michal Zalewski <lcamtuf@coredump.cx>

   Distributed under the terms and conditions of GNU LGPL.

 */

#define _GNU_SOURCE
#define _FROM_P0F

#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <signal.h>
#include <getopt.h>
#include <errno.h>
#include <dirent.h>
#include <pwd.h>
#include <grp.h>
#include <poll.h>
#include <time.h>
#include <locale.h>
#include <jansson.h>

#include <sys/types.h>
#include <sys/socket.h>
#include <arpa/inet.h>
#include <sys/un.h>
#include <sys/fcntl.h>
#include <sys/stat.h>
#include <sys/file.h>
#include <sys/wait.h>
#include <netinet/in.h>

#include <pcap.h>

#ifdef NET_BPF
#  include <net/bpf.h>
#else
#  include <pcap-bpf.h>
#endif /* !NET_BPF */

#include "types.h"
#include "debug.h"
#include "alloc-inl.h"
#include "process.h"
#include "readfp.h"
#include "api.h"
#include "tcp.h"
#include "fp_http.h"
#include "p0f.h"

#ifndef PF_INET6
#  define PF_INET6          10
#endif /* !PF_INET6 */

#ifndef O_NOFOLLOW
#  define O_NOFOLLOW 0
#endif /* !O_NOFOLLOW */

#ifndef O_LARGEFILE
#  define O_LARGEFILE 0
#endif /* !O_LARGEFILE */

static json_t *json_record = NULL;

static u8 *use_iface,                   /* Interface to listen on             */
          *orig_rule,                   /* Original filter rule               */
          *switch_user,                 /* Target username                    */
          *log_file,                    /* Binary log file name               */
          *api_sock,                    /* API socket file name               */
          *fp_file;                     /* Location of p0f.fp                 */

u8* read_file;                          /* File to read pcap data from        */

static u32
  api_max_conn    = API_MAX_CONN;       /* Maximum number of API connections  */

u32
  max_conn        = MAX_CONN,           /* Connection entry count limit       */
  max_hosts       = MAX_HOSTS,          /* Host cache entry count limit       */
  conn_max_age    = CONN_MAX_AGE,       /* Maximum age of a connection entry  */
  host_idle_limit = HOST_IDLE_LIMIT;    /* Host cache idle timeout            */

static struct api_client *api_cl;       /* Array with API client state        */
          
static s32 null_fd = -1,                /* File descriptor of /dev/null       */
           api_fd = -1;                 /* API socket descriptor              */

static FILE* lf;                        /* Log file stream                    */

static u8 stop_soon;                    /* Ctrl-C or so pressed?              */

u8 daemon_mode;                         /* Running in daemon mode?            */
u8 json_mode;                           /* Log in JSON?                       */
u8 line_buffered_mode;                  /* Line Buffered Mode?                */

static u8 set_promisc;                  /* Use promiscuous mode?              */

static pcap_t *pt;                      /* PCAP capture thingy                */

s32 link_type;                          /* PCAP link type                     */

u32 hash_seed;                          /* Hash seed                          */

static u8 obs_fields;                   /* No of pending observation fields   */

/* Memory allocator data: */

#ifdef DEBUG_BUILD
struct TRK_obj* TRK[ALLOC_BUCKETS];
u32 TRK_cnt[ALLOC_BUCKETS];
#endif /* DEBUG_BUILD */

#define LOGF(_x...) fprintf(lf, _x)

/* Display usage information */

static void usage(void) {

  ERRORF(

"Usage: p0f [ ...options... ] [ 'filter rule' ]\n"
"\n"
"Network interface options:\n"
"\n"
"  -i iface  - listen on the specified network interface\n"
"  -r file   - read offline pcap data from a given file\n"
"  -p        - put the listening interface in promiscuous mode\n"
"  -L        - list all available interfaces\n"
"\n"
"Operating mode and output settings:\n"
"\n"
"  -f file   - read fingerprint database from 'file' (%s)\n"
"  -o file   - write information to the specified log file\n"
"  -j        - Log in JSON format.\n"
"  -l        - Line buffered mode for logging to output file.\n"
#ifndef __CYGWIN__
"  -s name   - answer to API queries at a named unix socket\n"
#endif /* !__CYGWIN__ */
"  -u user   - switch to the specified unprivileged account and chroot\n"
"  -d        - fork into background (requires -o or -s)\n"
"\n"
"Performance-related options:\n"
"\n"
#ifndef __CYGWIN__
"  -S limit  - limit number of parallel API connections (%u)\n"
#endif /* !__CYGWIN__ */
"  -t c,h    - set connection / host cache age limits (%us,%um)\n"
"  -m c,h    - cap the number of active connections / hosts (%u,%u)\n"
"\n"
"Optional filter expressions (man tcpdump) can be specified in the command\n"
"line to prevent p0f from looking at incidental network traffic.\n"
"\n"
"Problems? You can reach the author at <lcamtuf@coredump.cx>.\n",

    FP_FILE,
#ifndef __CYGWIN__
    API_MAX_CONN,
#endif /* !__CYGWIN__ */
    CONN_MAX_AGE, HOST_IDLE_LIMIT, MAX_CONN,  MAX_HOSTS);

  exit(1);

}


/* Obtain hash seed: */

static void get_hash_seed(void) {

  s32 f = open("/dev/urandom", O_RDONLY);

  if (f < 0) PFATAL("Cannot open /dev/urandom for reading.");

#ifndef DEBUG_BUILD

  /* In debug versions, use a constant seed. */

  if (read(f, &hash_seed, sizeof(hash_seed)) != sizeof(hash_seed))
    FATAL("Cannot read data from /dev/urandom.");

#endif /* !DEBUG_BUILD */

  close(f);

}


/* Get rid of unnecessary file descriptors */

static void close_spare_fds(void) {

  s32 i, closed = 0;
  DIR* d;
  struct dirent* de;

  d = opendir("/proc/self/fd");

  if (!d) {
    /* Best we could do... */
    for (i = 3; i < 256; i++) 
      if (!close(i)) closed++;
    return;
  }

  while ((de = readdir(d))) {
    i = atol(de->d_name);
    if (i > 2 && !close(i)) closed++;
  }

  closedir(d);

  if (closed)
    SAYF("[+] Closed %u file descriptor%s.\n", closed, closed == 1 ? "" : "s" );

}


/* Create or open log file */

static void open_log(void) {

  struct stat st;
  s32 log_fd;

  log_fd = open((char*)log_file, O_WRONLY | O_APPEND | O_NOFOLLOW | O_LARGEFILE);

  if (log_fd >= 0) {

    if (fstat(log_fd, &st)) PFATAL("fstat() on '%s' failed.", log_file);

    if (!S_ISREG(st.st_mode)) FATAL("'%s' is not a regular file.", log_file);

  } else {

    if (errno != ENOENT) PFATAL("Cannot open '%s'.", log_file);

    log_fd = open((char*)log_file, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW,
                  LOG_MODE);

    if (log_fd < 0) PFATAL("Cannot open '%s'.", log_file);

  }

  if (flock(log_fd, LOCK_EX | LOCK_NB))
    FATAL("'%s' is being used by another process.", log_file);

  lf = fdopen(log_fd, "a");

  if (!lf) FATAL("fdopen() on '%s' failed.", log_file);

  SAYF("[+] Log file '%s' opened for writing.\n", log_file);

}


/* Create and start listening on API socket */

static void open_api(void) {

  s32 old_umask;
  u32 i;

  struct sockaddr_un u;
  struct stat st;

  api_fd = socket(PF_UNIX, SOCK_STREAM, 0);

  if (api_fd < 0) PFATAL("socket(PF_UNIX) failed.");

  memset(&u, 0, sizeof(u));
  u.sun_family = AF_UNIX;

  if (strlen((char*)api_sock) >= sizeof(u.sun_path))
    FATAL("API socket filename is too long for sockaddr_un (blame Unix).");

  strcpy(u.sun_path, (char*)api_sock);

  /* This is bad, but you can't do any better with standard unix socket
     semantics today :-( */

  if (!stat((char*)api_sock, &st) && !S_ISSOCK(st.st_mode))
    FATAL("'%s' exists but is not a socket.", api_sock);

  if (unlink((char*)api_sock) && errno != ENOENT)
    PFATAL("unlink('%s') failed.", api_sock);

  old_umask = umask(0777 ^ API_MODE);

  if (bind(api_fd, (struct sockaddr*)&u, sizeof(u)))
    PFATAL("bind() on '%s' failed.", api_sock);
  
  umask(old_umask);

  if (listen(api_fd, api_max_conn))
    PFATAL("listen() on '%s' failed.", api_sock);

  if (fcntl(api_fd, F_SETFL, O_NONBLOCK))
    PFATAL("fcntl() to set O_NONBLOCK on API listen socket fails.");

  api_cl = DFL_ck_alloc(api_max_conn * sizeof(struct api_client));

  for (i = 0; i < api_max_conn; i++) api_cl[i].fd = -1;

  SAYF("[+] Listening on API socket '%s' (max %u clients).\n",
       api_sock, api_max_conn);

}


/* Open log entry. */

void start_observation(char* keyword, u8 field_cnt, u8 to_srv,
                       struct packet_flow* f) {

  if (obs_fields) FATAL("Premature end of observation.");

  if (!daemon_mode) {

    SAYF(".-[ %s/%u -> ", addr_to_str(f->client->addr, f->client->ip_ver),
         f->cli_port);
    SAYF("%s/%u (%s) ]-\n|\n", addr_to_str(f->server->addr, f->client->ip_ver),
         f->srv_port, keyword);

    SAYF("| %-8s = %s/%u\n", to_srv ? "client" : "server", 
         addr_to_str(to_srv ? f->client->addr :
         f->server->addr, f->client->ip_ver),
         to_srv ? f->cli_port : f->srv_port);

  }

  if (log_file) {

    u8 tmp[64];

    time_t ut = get_unix_time();
    struct tm* lt = localtime(&ut);

    strftime((char*)tmp, 64, "%Y/%m/%d %H:%M:%S", lt);

    if (json_mode) {
        json_record = json_object();
        json_object_set_new(json_record, "timestamp", json_string((char *)tmp));
        json_object_set_new(json_record, "mod", json_string((char *)keyword));
        json_object_set_new(json_record, "client_ip", json_string((char *)addr_to_str(f->client->addr, f->client->ip_ver)));
        json_object_set_new(json_record, "server_ip", json_string((char *)addr_to_str(f->server->addr, f->server->ip_ver)));
        json_object_set_new(json_record, "client_port", json_integer(f->cli_port));
        json_object_set_new(json_record, "server_port", json_integer(f->srv_port));
        json_object_set_new(json_record, "subject", json_string((char *)(to_srv ? "cli" : "srv")));
    } else {
      LOGF("[%s] mod=%s|cli=%s/%u|",tmp, keyword, addr_to_str(f->client->addr,
         f->client->ip_ver), f->cli_port);

      LOGF("srv=%s/%u|subj=%s", addr_to_str(f->server->addr, f->server->ip_ver),
         f->srv_port, to_srv ? "cli" : "srv");
    }
  }

  obs_fields = field_cnt;
}


/* Add log item. */

void add_observation_field(char* key, u8* value) {

  if (!obs_fields) FATAL("Unexpected observation field ('%s').", key);

  if (!daemon_mode)
    SAYF("| %-8s = %s\n", key, value ? value : (u8*)"???");

  if (log_file) {
    if (json_mode) {
      json_object_set_new(json_record, key, json_string( (char *)(value ? value : (u8*)"???") ));
    } else {
      LOGF("|%s=%s", key, value ? value : (u8*)"???");
    }
  }

  obs_fields--;

  if (!obs_fields) {

    if (!daemon_mode) SAYF("|\n`----\n\n");

    if (log_file){

      if (json_mode) {
        json_dumpf(json_record, lf, 0);
        json_decref(json_record);
      }

      LOGF("\n");
      if (line_buffered_mode) {
        fflush(lf);
      }
    }

  }

}


/* Show PCAP interface list */

static void list_interfaces(void) {

  char pcap_err[PCAP_ERRBUF_SIZE];
  pcap_if_t *dev;
  u8 i = 0;

  /* There is a bug in several years' worth of libpcap releases that causes it
     to SEGV here if /sys/class/net is not readable. See http://goo.gl/nEnGx */

  if (access("/sys/class/net", R_OK | X_OK) && errno != ENOENT)
    FATAL("This operation requires access to /sys/class/net/, sorry.");

  if (pcap_findalldevs(&dev, pcap_err) == -1)
    FATAL("pcap_findalldevs: %s\n", pcap_err);

  if (!dev) FATAL("Can't find any interfaces. Maybe you need to be root?");

  SAYF("\n-- Available interfaces --\n");

  do {

    pcap_addr_t *a = dev->addresses;

    SAYF("\n%3d: Name        : %s\n", i++, dev->name);
    SAYF("     Description : %s\n", dev->description ? dev->description : "-");

    /* Let's try to find something we can actually display. */

    while (a && a->addr->sa_family != PF_INET && a->addr->sa_family != PF_INET6)
      a = a->next;

    if (a) {

      if (a->addr->sa_family == PF_INET)
        SAYF("     IP address  : %s\n", addr_to_str(((u8*)a->addr) + 4, IP_VER4));
      else
        SAYF("     IP address  : %s\n", addr_to_str(((u8*)a->addr) + 8, IP_VER6));

     } else SAYF("     IP address  : (none)\n");

  } while ((dev = dev->next));

  SAYF("\n");

  pcap_freealldevs(dev);

}



#ifdef __CYGWIN__

/* List PCAP-recognized interfaces */

static u8* find_interface(int num) {

  char pcap_err[PCAP_ERRBUF_SIZE];
  pcap_if_t *dev;

  if (pcap_findalldevs(&dev, pcap_err) == -1)
    FATAL("pcap_findalldevs: %s\n", pcap_err);

  do {

    if (!num--) {
      u8* ret = DFL_ck_strdup((char*)dev->name);
      pcap_freealldevs(dev);
      return ret;
    }

  } while ((dev = dev->next));

  FATAL("Interface not found (use -L to list all).");

}

#endif /* __CYGWIN__ */


/* Initialize PCAP capture */

static void prepare_pcap(void) {

  char pcap_err[PCAP_ERRBUF_SIZE];
  u8* orig_iface = use_iface;

  if (read_file) {

    if (set_promisc)
      FATAL("Dude, how am I supposed to make a file promiscuous?");

    if (use_iface)
      FATAL("Options -i and -r are mutually exclusive.");

    if (access((char*)read_file, R_OK))
      PFATAL("Can't access file '%s'.", read_file);

    pt = pcap_open_offline((char*)read_file, pcap_err);

    if (!pt) FATAL("pcap_open_offline: %s", pcap_err);

    SAYF("[+] Will read pcap data from file '%s'.\n", read_file);

  } else {

    if (!use_iface) {

      /* See the earlier note on libpcap SEGV - same problem here.
         Also, this returns something stupid on Windows, but hey... */
     
      if (!access("/sys/class/net", R_OK | X_OK) || errno == ENOENT)
        use_iface = (u8*)pcap_lookupdev(pcap_err);

      if (!use_iface)
        FATAL("libpcap is out of ideas; use -i to specify interface.");

    }

#ifdef __CYGWIN__

    /* On Windows, interface names are unwieldy, and people prefer to use
       numerical IDs. */

    else {

      int iface_id;

      if (sscanf((char*)use_iface, "%u", &iface_id) == 1) {
        use_iface = find_interface(iface_id);
      }
  
    }

    pt = pcap_open_live((char*)use_iface, SNAPLEN, set_promisc, 250, pcap_err);

#else 

    /* PCAP timeouts tend to be broken, so we'll use a very small value
       and rely on select() instead. */

    pt = pcap_open_live((char*)use_iface, SNAPLEN, set_promisc, 5, pcap_err);

#endif /* ^__CYGWIN__ */

    if (!orig_iface)
      SAYF("[+] Intercepting traffic on default interface '%s'.\n", use_iface);
    else
      SAYF("[+] Intercepting traffic on interface '%s'.\n", use_iface);

    if (!pt) FATAL("pcap_open_live: %s", pcap_err);

  }

  link_type = pcap_datalink(pt);

}


/* Initialize BPF filtering */

static void prepare_bpf(void) {

  struct bpf_program flt;

  u8*  final_rule;
  u8   vlan_support;

  /* VLAN matching is somewhat brain-dead: you need to request it explicitly,
     and it alters the semantics of the remainder of the expression. */

  vlan_support = (pcap_datalink(pt) == DLT_EN10MB);

retry_no_vlan:

  if (!orig_rule) {

    if (vlan_support) {
      final_rule = (u8*)"tcp or (vlan and tcp)";
    } else {
      final_rule = (u8*)"tcp";
    }

  } else {

    if (vlan_support) {

      final_rule = ck_alloc(strlen((char*)orig_rule) * 2 + 64);

      sprintf((char*)final_rule, "(tcp and (%s)) or (vlan and tcp and (%s))",
              orig_rule, orig_rule);

    } else {

      final_rule = ck_alloc(strlen((char*)orig_rule) + 16);

      sprintf((char*)final_rule, "tcp and (%s)", orig_rule);

    }

  }

  DEBUG("[#] Computed rule: %s\n", final_rule);

  if (pcap_compile(pt, &flt, (char*)final_rule, 1, 0)) {

    if (vlan_support) {

      if (orig_rule) ck_free(final_rule);
      vlan_support = 0;
      goto retry_no_vlan;

    }

    pcap_perror(pt, "[-] pcap_compile");

    if (!orig_rule)
      FATAL("pcap_compile() didn't work, strange");
    else
      FATAL("Syntax error! See 'man tcpdump' for help on filters.");

  }

  if (pcap_setfilter(pt, &flt))
    FATAL("pcap_setfilter() didn't work, strange.");

  pcap_freecode(&flt);

  if (!orig_rule) {

    SAYF("[+] Default packet filtering configured%s.\n",
         vlan_support ? " [+VLAN]" : "");

  } else {

    SAYF("[+] Custom filtering rule enabled: %s%s\n",
         orig_rule ? orig_rule : (u8*)"tcp",
         vlan_support ? " [+VLAN]" : "");

    ck_free(final_rule);

  }

}


/* Drop privileges and chroot(), with some sanity checks */

static void drop_privs(void) {

  struct passwd* pw;

  pw = getpwnam((char*)switch_user);

  if (!pw) FATAL("User '%s' not found.", switch_user);

  if (!strcmp(pw->pw_dir, "/"))
    FATAL("User '%s' must have a dedicated home directory.", switch_user);

  if (!pw->pw_uid || !pw->pw_gid)
    FATAL("User '%s' must be non-root.", switch_user);

  if (initgroups(pw->pw_name, pw->pw_gid))
    PFATAL("initgroups() for '%s' failed.", switch_user);

  if (chdir(pw->pw_dir))
    PFATAL("chdir('%s') failed.", pw->pw_dir);

  if (chroot(pw->pw_dir))
    PFATAL("chroot('%s') failed.", pw->pw_dir);

  if (chdir("/"))
    PFATAL("chdir('/') after chroot('%s') failed.", pw->pw_dir);

  if (!access("/proc/", F_OK) || !access("/sys/", F_OK))
    FATAL("User '%s' must have a dedicated home directory.", switch_user);

  if (setgid(pw->pw_gid))
    PFATAL("setgid(%u) failed.", pw->pw_gid);

  if (setuid(pw->pw_uid))
    PFATAL("setuid(%u) failed.", pw->pw_uid);

  if (getegid() != pw->pw_gid || geteuid() != pw->pw_uid)
    FATAL("Inconsistent euid / egid after dropping privs.");

  SAYF("[+] Privileges dropped: uid %u, gid %u, root '%s'.\n",
       pw->pw_uid, pw->pw_gid, pw->pw_dir);

}


/* Enter daemon mode. */

static void fork_off(void) {

  s32 npid;

  fflush(0);

  npid = fork();

  if (npid < 0) PFATAL("fork() failed.");

  if (!npid) {

    /* Let's assume all this is fairly unlikely to fail, so we can live
       with the parent possibly proclaiming success prematurely. */

    if (dup2(null_fd, 0) < 0) PFATAL("dup2() failed.");

    /* If stderr is redirected to a file, keep that fd and use it for
       normal output. */

    if (isatty(2)) {

      if (dup2(null_fd, 1) < 0 || dup2(null_fd, 2) < 0)
        PFATAL("dup2() failed.");

    } else {

      if (dup2(2, 1) < 0) PFATAL("dup2() failed.");

    }

    close(null_fd);
    null_fd = -1;

    if (chdir("/")) PFATAL("chdir('/') failed.");

    setsid();

  } else {

    SAYF("[+] Daemon process created, PID %u (stderr %s).\n", npid,
      isatty(2) ? "not kept" : "kept as-is");

    SAYF("\nGood luck, you're on your own now!\n");

    exit(0);

  }

}


/* Handler for Ctrl-C and related signals */

static void abort_handler(int sig) {
  if (stop_soon) exit(1);
  stop_soon = 1;
}


#ifndef __CYGWIN__

/* Regenerate pollfd data for poll() */

static u32 regen_pfds(struct pollfd* pfds, struct api_client** ctable) {
  u32 i, count = 2;

  pfds[0].fd     = pcap_fileno(pt);
  pfds[0].events = (POLLIN | POLLERR | POLLHUP);

  DEBUG("[#] Recomputing pollfd data, pcap_fd = %d.\n", pfds[0].fd);

  if (!api_sock) return 1;

  pfds[1].fd     = api_fd;
  pfds[1].events = (POLLIN | POLLERR | POLLHUP);

  for (i = 0; i < api_max_conn; i++) {

    if (api_cl[i].fd == -1) continue;

    ctable[count] = api_cl + i;

    /* If we haven't received a complete query yet, wait for POLLIN.
       Otherwise, we want to write stuff. */

    if (api_cl[i].in_off < sizeof(struct p0f_api_query))
      pfds[count].events = (POLLIN | POLLERR | POLLHUP);
    else
      pfds[count].events = (POLLOUT | POLLERR | POLLHUP);

    pfds[count++].fd   = api_cl[i].fd;

  }

  return count;

}

#endif /* !__CYGWIN__ */


/* Event loop! Accepts and dispatches pcap data, API queries, etc. */

static void live_event_loop(void) {

#ifndef __CYGWIN__

  /* The huge problem with winpcap on cygwin is that you can't get a file
     descriptor suitable for poll() / select() out of it:

     http://www.winpcap.org/pipermail/winpcap-users/2009-April/003179.html

     The only alternatives seem to be additional processes / threads, a
     nasty busy loop, or a ton of Windows-specific code. If you need APi
     queries on Windows, you are welcome to fix this :-) */

  struct pollfd *pfds;
  struct api_client** ctable;
  u32 pfd_count;

  /* We need room for pcap, and possibly api_fd + api_clients. */

  pfds = ck_alloc((1 + (api_sock ? (1 + api_max_conn) : 0)) *
                  sizeof(struct pollfd));

  ctable = ck_alloc((1 + (api_sock ? (1 + api_max_conn) : 0)) *
                    sizeof(struct api_client*));

  pfd_count = regen_pfds(pfds, ctable);

  if (!daemon_mode) 
    SAYF("[+] Entered main event loop.\n\n");

  while (!stop_soon) {

    s32 pret, i;
    u32 cur;

    /* We had a 250 ms timeout to keep Ctrl-C responsive without resortng
       to silly sigaction hackery or unsafe signal handler code. Unfortunately,
       if poll() timeout is much longer than pcap timeout, we end up with
       dropped packets on VMs. Seems like a kernel bug, but for now, this
       loop is a bit busier than it needs to be... */

poll_again:

    pret = poll(pfds, pfd_count, 10);

    if (pret < 0) {
      if (errno == EINTR) break;
      PFATAL("poll() failed.");
    }

    if (!pret) { if (log_file) fflush(lf); continue; }

    /* Examine pfds... */

    for (cur = 0; cur < pfd_count; cur++) {

      if (pfds[cur].revents & (POLLERR | POLLHUP)) switch (cur) {

        case 0:

          FATAL("Packet capture interface is down.");

        case 1:

          FATAL("API socket is down.");

        default:

          /* Shut down API connection and free its state. */

          DEBUG("[#] API connection on fd %d closed.\n", pfds[cur].fd);

          close(pfds[cur].fd);
          ctable[cur]->fd = -1;
 
          pfd_count = regen_pfds(pfds, ctable);
          goto poll_again;

      }

      if (pfds[cur].revents & POLLOUT) switch (cur) {

        case 0: case 1:

          FATAL("Unexpected POLLOUT on fd %d.\n", cur);

        default:

          /* Write API response, restart state when complete. */

          if (ctable[cur]->in_off < sizeof(struct p0f_api_query))
            FATAL("Inconsistent p0f_api_response state.\n");

          i = write(pfds[cur].fd, 
                   ((char*)&ctable[cur]->out_data) + ctable[cur]->out_off,
                   sizeof(struct p0f_api_response) - ctable[cur]->out_off);

          if (i <= 0) PFATAL("write() on API socket fails despite POLLOUT.");

          ctable[cur]->out_off += i;

          /* All done? Back to square zero then! */

          if (ctable[cur]->out_off == sizeof(struct p0f_api_response)) {

             ctable[cur]->in_off = ctable[cur]->out_off = 0;
             pfds[cur].events   = (POLLIN | POLLERR | POLLHUP);

          }

      }

      if (pfds[cur].revents & POLLIN) switch (cur) {
 
        case 0:

          /* Process traffic on the capture interface. */

          if (pcap_dispatch(pt, -1, (pcap_handler)parse_packet, 0) < 0)
            FATAL("Packet capture interface is down.");

          break;

        case 1:

          /* Accept new API connection, limits permitting. */

          if (!api_sock) FATAL("Unexpected API connection.");

          if (pfd_count - 2 < api_max_conn) {

            for (i = 0; i < api_max_conn && api_cl[i].fd >= 0; i++);

            if (i == api_max_conn) FATAL("Inconsistent API connection data.");

            api_cl[i].fd = accept(api_fd, NULL, NULL);

            if (api_cl[i].fd < 0) {

              WARN("Unable to handle API connection: accept() fails.");

            } else {

              if (fcntl(api_cl[i].fd, F_SETFL, O_NONBLOCK))
                PFATAL("fcntl() to set O_NONBLOCK on API connection fails.");

              api_cl[i].in_off = api_cl[i].out_off = 0;
              pfd_count = regen_pfds(pfds, ctable);

              DEBUG("[#] Accepted new API connection, fd %d.\n", api_cl[i].fd);

              goto poll_again;

            }

          } else WARN("Too many API connections (use -S to adjust).\n");

          break;

        default:

          /* Receive API query, dispatch when complete. */

          if (ctable[cur]->in_off >= sizeof(struct p0f_api_query))
            FATAL("Inconsistent p0f_api_query state.\n");

          i = read(pfds[cur].fd, 
                   ((char*)&ctable[cur]->in_data) + ctable[cur]->in_off,
                   sizeof(struct p0f_api_query) - ctable[cur]->in_off);

          if (i < 0) PFATAL("read() on API socket fails despite POLLIN.");

          ctable[cur]->in_off += i;

          /* Query in place? Compute response and prepare to send it back. */

          if (ctable[cur]->in_off == sizeof(struct p0f_api_query)) {

            handle_query(&ctable[cur]->in_data, &ctable[cur]->out_data);
            pfds[cur].events = (POLLOUT | POLLERR | POLLHUP);

          }

      }


      /* Processed all reported updates already? If so, bail out early. */

      if (pfds[cur].revents && !--pret) break;

    }

  }

  ck_free(ctable);
  ck_free(pfds);

#else

  if (!daemon_mode) 
    SAYF("[+] Entered main event loop.\n\n");

  /* Ugh. The only way to keep SIGINT and other signals working is to have this
     funny loop with dummy I/O every 250 ms. Signal handlers don't get called
     in pcap_dispatch() or pcap_loop() unless there's I/O. */

  while (!stop_soon) {

    s32 ret = pcap_dispatch(pt, -1, (pcap_handler)parse_packet, 0);

    if (ret < 0) return;

    if (log_file && !ret) fflush(lf);

    write(2, NULL, 0);

  }

#endif /* ^!__CYGWIN__ */

  WARN("User-initiated shutdown.");

}


/* Simple event loop for processing offline captures. */

static void offline_event_loop(void) {

  if (!daemon_mode) 
    SAYF("[+] Processing capture data.\n\n");

  while (!stop_soon)  {

    if (pcap_dispatch(pt, -1, (pcap_handler)parse_packet, 0) <= 0) return;

  }

  WARN("User-initiated shutdown.");

}


/* Main entry point */

int main(int argc, char** argv) {

  s32 r;

  setlinebuf(stdout);

  SAYF("--- p0f " VERSION " by Michal Zalewski <lcamtuf@coredump.cx> ---\n\n");

  if (getuid() != geteuid())
    FATAL("Please don't make me setuid. See README for more.\n");

  while ((r = getopt(argc, argv, "+LS:djlf:i:m:o:pr:s:t:u:")) != -1) switch (r) {

    case 'L':

      list_interfaces();
      exit(0);

    case 'S':

#ifdef __CYGWIN__

      FATAL("API mode not supported on Windows (see README).");

#else

      if (api_max_conn != API_MAX_CONN)
        FATAL("Multiple -S options not supported.");

      api_max_conn = atol(optarg);

      if (!api_max_conn || api_max_conn > 100)
        FATAL("Outlandish value specified for -S.");

      break;

#endif /* ^__CYGWIN__ */


    case 'd':

      if (daemon_mode)
        FATAL("Double werewolf mode not supported yet.");

      daemon_mode = 1;
      break;

    case 'f':

      if (fp_file)
        FATAL("Multiple -f options not supported.");

      fp_file = (u8*)optarg;
      break;

    case 'i':

      if (use_iface)
        FATAL("Multiple -i options not supported (try '-i any').");

      use_iface = (u8*)optarg;

      break;

    case 'j':

      if (json_mode)
        FATAL("Double werewolf mode not supported yet.");

      json_mode = 1;
      break;

    case 'l':

      if (line_buffered_mode)
        FATAL("Double werewolf mode not supported yet.");

      line_buffered_mode = 1;
      break;

    case 'm':

      if (max_conn != MAX_CONN || max_hosts != MAX_HOSTS)
        FATAL("Multiple -m options not supported.");

      if (sscanf(optarg, "%u,%u", &max_conn, &max_hosts) != 2 ||
          !max_conn || max_conn > 100000 ||
          !max_hosts || max_hosts > 500000)
        FATAL("Outlandish value specified for -m.");

      break;

    case 'o':

      if (log_file)
        FATAL("Multiple -o options not supported.");

      log_file = (u8*)optarg;

      break;

    case 'p':
    
      if (set_promisc)
        FATAL("Even more promiscuous? People will start talking!");

      set_promisc = 1;
      break;

    case 'r':

      if (read_file)
        FATAL("Multiple -r options not supported.");

      read_file = (u8*)optarg;

      break;

    case 's':

#ifdef __CYGWIN__

      FATAL("API mode not supported on Windows (see README).");

#else

      if (api_sock) 
        FATAL("Multiple -s options not supported.");

      api_sock = (u8*)optarg;

      break;

#endif /* ^__CYGWIN__ */

    case 't':

      if (conn_max_age != CONN_MAX_AGE || host_idle_limit != HOST_IDLE_LIMIT)
        FATAL("Multiple -t options not supported.");

      if (sscanf(optarg, "%u,%u", &conn_max_age, &host_idle_limit) != 2 ||
          !conn_max_age || conn_max_age > 1000000 ||
          !host_idle_limit || host_idle_limit > 1000000)
        FATAL("Outlandish value specified for -t.");

      break;

    case 'u':

      if (switch_user)
        FATAL("Split personality mode not supported.");

      switch_user = (u8*)optarg;

      break;

    default: usage();

  }

  if (optind < argc) {

    if (optind + 1 == argc) orig_rule = (u8*)argv[optind];
    else FATAL("Filter rule must be a single parameter (use quotes).");

  }

  if (read_file && api_sock)
    FATAL("API mode looks down on ofline captures.");

  if (!api_sock && api_max_conn != API_MAX_CONN)
    FATAL("Option -S makes sense only with -s.");

  if (daemon_mode) {

    if (read_file)
      FATAL("Daemon mode and offline captures don't mix.");

    if (!log_file && !api_sock)
      FATAL("Daemon mode requires -o or -s.");

#ifdef __CYGWIN__

    if (switch_user) 
      SAYF("[!] Note: under cygwin, -u is largely useless.\n");

#else

    if (!switch_user) 
      SAYF("[!] Consider specifying -u in daemon mode (see README).\n");

#endif /* ^__CYGWIN__ */

  }

  tzset();
  setlocale(LC_TIME, "C");

  close_spare_fds();

  get_hash_seed();

  http_init();

  read_config(fp_file ? fp_file : (u8*)FP_FILE);

  prepare_pcap();
  prepare_bpf();

  if (log_file) open_log();
  if (api_sock) open_api();
  
  if (daemon_mode) {
    null_fd = open("/dev/null", O_RDONLY);
    if (null_fd < 0) PFATAL("Cannot open '/dev/null'.");
  }
  
  if (switch_user) drop_privs();

  if (daemon_mode) fork_off();

  signal(SIGHUP, daemon_mode ? SIG_IGN : abort_handler);
  signal(SIGINT, abort_handler);
  signal(SIGTERM, abort_handler);

  if (read_file) offline_event_loop(); else live_event_loop();

  if (!daemon_mode)
    SAYF("\nAll done. Processed %llu packets.\n", packet_cnt);

#ifdef DEBUG_BUILD
  destroy_all_hosts();
  TRK_report();
#endif /* DEBUG_BUILD */

  return 0;

}
