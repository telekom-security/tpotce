/*
   p0f-client - simple API client
   ------------------------------

   Can be used to query p0f API sockets.

   Copyright (C) 2012 by Michal Zalewski <lcamtuf@coredump.cx>

   Distributed under the terms and conditions of GNU LGPL.

 */

#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <string.h>
#include <netdb.h>
#include <errno.h>
#include <ctype.h>
#include <time.h>

#include <netinet/in.h>
#include <arpa/inet.h>
#include <sys/types.h>
#include <sys/time.h>
#include <sys/socket.h>
#include <sys/un.h>

#include "../types.h"
#include "../config.h"
#include "../alloc-inl.h"
#include "../debug.h"
#include "../api.h"

/* Parse IPv4 address into a buffer. */

static void parse_addr4(char* str, u8* ret) {

  u32 a1, a2, a3, a4;

  if (sscanf(str, "%u.%u.%u.%u", &a1, &a2, &a3, &a4) != 4)
    FATAL("Malformed IPv4 address.");

  if (a1 > 255 || a2 > 255 || a3 > 255 || a4 > 255)
    FATAL("Malformed IPv4 address.");

  ret[0] = a1;
  ret[1] = a2;
  ret[2] = a3;
  ret[3] = a4;

}


/* Parse IPv6 address into a buffer. */

static void parse_addr6(char* str, u8* ret) {

  u32 seg = 0;
  u32 val;

  while (*str) {

    if (seg == 8) FATAL("Malformed IPv6 address (too many segments).");

    if (sscanf((char*)str, "%x", &val) != 1 ||
        val > 65535) FATAL("Malformed IPv6 address (bad octet value).");

    ret[seg * 2] = val >> 8;
    ret[seg * 2 + 1] = val;

    seg++;

    while (isxdigit(*str)) str++;
    if (*str) str++;

  }

  if (seg != 8) FATAL("Malformed IPv6 address (don't abbreviate).");

}


int main(int argc, char** argv) {

  u8 tmp[128];
  struct tm* t;

  static struct p0f_api_query q;
  static struct p0f_api_response r;

  static struct sockaddr_un sun;

  s32  sock;
  time_t ut;

  if (argc != 3) {
    ERRORF("Usage: p0f-client /path/to/socket host_ip\n");
    exit(1);
  }

  q.magic = P0F_QUERY_MAGIC;

  if (strchr(argv[2], ':')) {

    parse_addr6(argv[2], q.addr);
    q.addr_type = P0F_ADDR_IPV6;

  } else {

    parse_addr4(argv[2], q.addr);
    q.addr_type = P0F_ADDR_IPV4;

  }

  sock = socket(PF_UNIX, SOCK_STREAM, 0);

  if (sock < 0) PFATAL("Call to socket() failed.");

  sun.sun_family = AF_UNIX;

  if (strlen(argv[1]) >= sizeof(sun.sun_path))
    FATAL("API socket filename is too long for sockaddr_un (blame Unix).");

  strcpy(sun.sun_path, argv[1]);

  if (connect(sock, (struct sockaddr*)&sun, sizeof(sun)))
    PFATAL("Can't connect to API socket.");

  if (write(sock, &q, sizeof(struct p0f_api_query)) !=
      sizeof(struct p0f_api_query)) FATAL("Short write to API socket.");

  if (read(sock, &r, sizeof(struct p0f_api_response)) !=
      sizeof(struct p0f_api_response)) FATAL("Short read from API socket.");
  
  close(sock);

  if (r.magic != P0F_RESP_MAGIC)
    FATAL("Bad response magic (0x%08x).\n", r.magic);

  if (r.status == P0F_STATUS_BADQUERY)
    FATAL("P0f did not understand the query.\n");

  if (r.status == P0F_STATUS_NOMATCH) {
    SAYF("No matching host in p0f cache. That's all we know.\n");
    return 0;
  }

  ut = r.first_seen;
  t = localtime(&ut);
  strftime((char*)tmp, 128, "%Y/%m/%d %H:%M:%S", t);

  SAYF("First seen    = %s\n", tmp);

  ut = r.last_seen;
  t = localtime(&ut);
  strftime((char*)tmp, 128, "%Y/%m/%d %H:%M:%S", t);

  SAYF("Last update   = %s\n", tmp);

  SAYF("Total flows   = %u\n", r.total_conn);

  if (!r.os_name[0])
    SAYF("Detected OS   = ???\n");
  else
    SAYF("Detected OS   = %s %s%s%s\n", r.os_name, r.os_flavor,
         (r.os_match_q & P0F_MATCH_GENERIC) ? " [generic]" : "",
         (r.os_match_q & P0F_MATCH_FUZZY) ? " [fuzzy]" : "");

  if (!r.http_name[0])
    SAYF("HTTP software = ???\n");
  else
    SAYF("HTTP software = %s %s (ID %s)\n", r.http_name, r.http_flavor,
         (r.bad_sw == 2) ? "is fake" : (r.bad_sw ? "OS mismatch" : "seems legit"));

  if (!r.link_type[0])
    SAYF("Network link  = ???\n");
  else
    SAYF("Network link  = %s\n", r.link_type);

  if (!r.language[0])
    SAYF("Language      = ???\n");
  else
    SAYF("Language      = %s\n", r.language);


  if (r.distance == -1)
    SAYF("Distance      = ???\n");
  else
    SAYF("Distance      = %u\n", r.distance);

  if (r.last_nat) {
    ut = r.last_nat;
    t = localtime(&ut);
    strftime((char*)tmp, 128, "%Y/%m/%d %H:%M:%S", t);
    SAYF("IP sharing    = %s\n", tmp);
  }

  if (r.last_chg) {
    ut = r.last_chg;
    t = localtime(&ut);
    strftime((char*)tmp, 128, "%Y/%m/%d %H:%M:%S", t);
    SAYF("Sys change    = %s\n", tmp);
  }

  if (r.uptime_min) {
    SAYF("Uptime        = %u days %u hrs %u min (modulo %u days)\n", 
         r.uptime_min / 60 / 24, (r.uptime_min / 60) % 24, r.uptime_min % 60,
         r.up_mod_days);
  }

  return 0;

}

