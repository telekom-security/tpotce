/*
   p0f - packet capture and overall host / flow bookkeeping
   --------------------------------------------------------

   Copyright (C) 2012 by Michal Zalewski <lcamtuf@coredump.cx>

   Distributed under the terms and conditions of GNU LGPL.

 */

#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <pcap.h>
#include <time.h>
#include <ctype.h>

#include <sys/fcntl.h>
#include <netinet/in.h>
#include <sys/types.h>
#include <sys/socket.h>
#include <arpa/inet.h>
#include <sys/time.h>
#include <sys/stat.h>

#include "types.h"
#include "config.h"
#include "debug.h"
#include "alloc-inl.h"
#include "process.h"
#include "hash.h"
#include "tcp.h"
#include "readfp.h"
#include "p0f.h"

#include "fp_tcp.h"
#include "fp_mtu.h"
#include "fp_http.h"

u64 packet_cnt;                         /* Total number of packets processed  */

static s8 link_off = -1;                /* Link-specific IP header offset     */
static u8 bad_packets;                  /* Seen non-IP packets?               */

static struct host_data *host_by_age,   /* All host entries, by last mod      */
                        *newest_host;   /* Tail of the list                   */

static struct packet_flow *flow_by_age, /* All flows, by creation time        */
                          *newest_flow; /* Tail of the list                   */

static struct timeval* cur_time;        /* Current time, courtesy of pcap     */

/* Bucketed hosts and flows: */

static struct host_data    *host_b[HOST_BUCKETS];
static struct packet_flow  *flow_b[FLOW_BUCKETS];

static u32 host_cnt, flow_cnt;          /* Counters for bookkeeping purposes  */

static void flow_dispatch(struct packet_data* pk);
static void nuke_flows(u8 silent);
static void expire_cache(void);


/* Get unix time in milliseconds. */

u64 get_unix_time_ms(void) {

  return ((u64)cur_time->tv_sec) * 1000 + (cur_time->tv_usec / 1000);
}


/* Get unix time in seconds. */

u32 get_unix_time(void) {
  return cur_time->tv_sec;
}


/* Find link-specific offset (pcap knows, but won't tell). */

static void find_offset(const u8* data, s32 total_len) {

  u8 i;

  /* Check hardcoded values for some of the most common options. */

  switch (link_type) {

    case DLT_RAW:        link_off = 0;  return;

    case DLT_NULL:
    case DLT_PPP:        link_off = 4;  return;

    case DLT_LOOP:

#ifdef DLT_PPP_SERIAL
    case DLT_PPP_SERIAL:
#endif /* DLT_PPP_SERIAL */

    case DLT_PPP_ETHER:  link_off = 8;  return;

    case DLT_EN10MB:     link_off = 14; return;

#ifdef DLT_LINUX_SLL
    case DLT_LINUX_SLL:  link_off = 16; return;
#endif /* DLT_LINUX_SLL */

    case DLT_PFLOG:      link_off = 28; return;

    case DLT_IEEE802_11: link_off = 32; return;
  }

  /* If this fails, try to auto-detect. There is a slight risk that if the
     first packet we see is maliciously crafted, and somehow gets past the
     configured BPF filter, we will configure the wrong offset. But that
     seems fairly unlikely. */

  for (i = 0; i < 40; i += 2, total_len -= 2) {

    if (total_len < MIN_TCP4) break;

    /* Perhaps this is IPv6? We check three things: IP version (first 4 bits);
       total length sufficient to accommodate IPv6 and TCP headers; and the
       "next protocol" field equal to PROTO_TCP. */

    if (total_len >= MIN_TCP6 && (data[i] >> 4) == IP_VER6) {

      struct ipv6_hdr* hdr = (struct ipv6_hdr*)(data + i);

      if (hdr->proto == PROTO_TCP) {

        DEBUG("[#] Detected packet offset of %u via IPv6 (link type %u).\n", i,
              link_type);
        link_off = i;
        break;

      }
      
    }

    /* Okay, let's try IPv4 then. The same approach, except the shortest packet
       size must be just enough to accommodate IPv4 + TCP (already checked). */

    if ((data[i] >> 4) == IP_VER4) {

      struct ipv4_hdr* hdr = (struct ipv4_hdr*)(data + i);

      if (hdr->proto == PROTO_TCP) {

        DEBUG("[#] Detected packet offset of %u via IPv4 (link type %u).\n", i,
              link_type);
        link_off = i;
        break;

      }

    }

  }

  /* If we found something, adjust for VLAN tags (ETH_P_8021Q == 0x8100). Else,
     complain once and try again soon. */

  if (link_off >= 4 && data[i-4] == 0x81 && data[i-3] == 0x00) {

    DEBUG("[#] Adjusting offset due to VLAN tagging.\n");
    link_off -= 4;

  } else if (link_off == -1) {

    link_off = -2;
    WARN("Unable to find link-specific packet offset. This is bad.");

  }

}


/* Convert IPv4 or IPv6 address to a human-readable form. */

u8* addr_to_str(u8* data, u8 ip_ver) {

  static char tmp[128];

  /* We could be using inet_ntop(), but on systems that have older libc
     but still see passing IPv6 traffic, we would be in a pickle. */

  if (ip_ver == IP_VER4) {

    sprintf(tmp, "%u.%u.%u.%u", data[0], data[1], data[2], data[3]);

  } else {

    sprintf(tmp, "%x:%x:%x:%x:%x:%x:%x:%x",
            (data[0] << 8) | data[1], (data[2] << 8) | data[3], 
            (data[4] << 8) | data[5], (data[6] << 8) | data[7], 
            (data[8] << 8) | data[9], (data[10] << 8) | data[11], 
            (data[12] << 8) | data[13], (data[14] << 8) | data[15]);

  }

  return (u8*)tmp;

}


/* Parse PCAP input, with plenty of sanity checking. Store interesting details
   in a protocol-agnostic buffer that will be then examined upstream. */

void parse_packet(void* junk, const struct pcap_pkthdr* hdr, const u8* data) {

  struct tcp_hdr* tcp;
  struct packet_data pk;

  s32 packet_len;
  u32 tcp_doff;

  u8* opt_end;

  packet_cnt++;
  
  cur_time = (struct timeval*)&hdr->ts;

  if (!(packet_cnt % EXPIRE_INTERVAL)) expire_cache();

  /* Be paranoid about how much data we actually have off the wire. */

  packet_len = MIN(hdr->len, hdr->caplen);
  if (packet_len > SNAPLEN) packet_len = SNAPLEN;

  // DEBUG("[#] Received packet: len = %d, caplen = %d, limit = %d\n",
  //    hdr->len, hdr->caplen, SNAPLEN);

  /* Account for link-level headers. */

  if (link_off < 0) find_offset(data, packet_len);

  if (link_off > 0) {

    data += link_off;
    packet_len -= link_off;

  }

  /* If there is no way we could have received a complete TCP packet, bail
     out early. */

  if (packet_len < MIN_TCP4) {
    DEBUG("[#] Packet too short for any IPv4 + TCP headers, giving up!\n");
    return;
  }

  pk.quirks = 0;

  if ((*data >> 4) == IP_VER4) {

    /************************
     * IPv4 header parsing. *
     ************************/
    
    const struct ipv4_hdr* ip4 = (struct ipv4_hdr*)data;

    u32 hdr_len = (ip4->ver_hlen & 0x0F) * 4;
    u16 flags_off = ntohs(RD16(ip4->flags_off));
    u16 tot_len = ntohs(RD16(ip4->tot_len));

    /* If the packet claims to be shorter than what we received off the wire,
       honor this claim to account for etherleak-type bugs. */

    if (packet_len > tot_len) {
      packet_len = tot_len;
      // DEBUG("[#] ipv4.tot_len = %u, adjusted accordingly.\n", tot_len);
    }

    /* Bail out if the result leaves no room for IPv4 + TCP headers. */

    if (packet_len < MIN_TCP4) {
      DEBUG("[#] packet_len = %u. Too short for IPv4 + TCP, giving up!\n",
            packet_len);
      return;
    }

    /* Bail out if the declared length of IPv4 headers is nonsensical. */

    if (hdr_len < sizeof(struct ipv4_hdr)) {
      DEBUG("[#] ipv4.hdr_len = %u. Too short for IPv4, giving up!\n",
            hdr_len);
      return;
    }

    /* If the packet claims to be longer than the recv buffer, best to back
       off - even though we could just ignore this and recover. */

    if (tot_len > packet_len) {
      DEBUG("[#] ipv4.tot_len = %u but packet_len = %u, bailing out!\n",
            tot_len, packet_len);
      return;
    }

    /* And finally, bail out if after skipping the IPv4 header as specified
       (including options), there wouldn't be enough room for TCP. */

    if (hdr_len + sizeof(struct tcp_hdr) > packet_len) {
      DEBUG("[#] ipv4.hdr_len = %u, packet_len = %d, no room for TCP!\n",
            hdr_len, packet_len);
      return;
    }

    /* Bail out if the subsequent protocol is not TCP. */

    if (ip4->proto != PROTO_TCP) {
      DEBUG("[#] Whoa, IPv4 packet with non-TCP payload (%u)?\n", ip4->proto);
      return;
    }

    /* Ignore any traffic with MF or non-zero fragment offset specified. We
       can do enough just fingerprinting the non-fragmented traffic. */

    if (flags_off & ~(IP4_DF | IP4_MBZ)) {
      DEBUG("[#] Packet fragment (0x%04x), letting it slide!\n", flags_off);
      return;
    }

    /* Store some relevant information about the packet. */

    pk.ip_ver = IP_VER4;

    pk.ip_opt_len = hdr_len - 20;

    memcpy(pk.src, ip4->src, 4);
    memcpy(pk.dst, ip4->dst, 4);

    pk.tos = ip4->tos_ecn >> 2;

    pk.ttl = ip4->ttl;

    if (ip4->tos_ecn & (IP_TOS_CE | IP_TOS_ECT)) pk.quirks |= QUIRK_ECN;

    /* Tag some of the corner cases associated with implementation quirks. */
    
    if (flags_off & IP4_MBZ) pk.quirks |= QUIRK_NZ_MBZ;

    if (flags_off & IP4_DF) {

      pk.quirks |= QUIRK_DF;
      if (RD16(ip4->id)) pk.quirks |= QUIRK_NZ_ID;

    } else {

      if (!RD16(ip4->id)) pk.quirks |= QUIRK_ZERO_ID;

    }

    pk.tot_hdr = hdr_len;

    tcp = (struct tcp_hdr*)(data + hdr_len);
    packet_len -= hdr_len;
    
  } else if ((*data >> 4) == IP_VER6) {

    /************************
     * IPv6 header parsing. *
     ************************/
    
    const struct ipv6_hdr* ip6 = (struct ipv6_hdr*)data;
    u32 ver_tos = ntohl(RD32(ip6->ver_tos));
    u32 tot_len = ntohs(RD16(ip6->pay_len)) + sizeof(struct ipv6_hdr);

    /* If the packet claims to be shorter than what we received off the wire,
       honor this claim to account for etherleak-type bugs. */

    if (packet_len > tot_len) {
      packet_len = tot_len;
      // DEBUG("[#] ipv6.tot_len = %u, adjusted accordingly.\n", tot_len);
    }

    /* Bail out if the result leaves no room for IPv6 + TCP headers. */

    if (packet_len < MIN_TCP6) {
      DEBUG("[#] packet_len = %u. Too short for IPv6 + TCP, giving up!\n",
            packet_len);
      return;
    }

    /* If the packet claims to be longer than the data we have, best to back
       off - even though we could just ignore this and recover. */

    if (tot_len > packet_len) {
      DEBUG("[#] ipv6.tot_len = %u but packet_len = %u, bailing out!\n",
            tot_len, packet_len);
      return;
    }

    /* Bail out if the subsequent protocol is not TCP. One day, we may try
       to parse and skip IPv6 extensions, but there seems to be no point in
       it today. */

    if (ip6->proto != PROTO_TCP) {
      DEBUG("[#] IPv6 packet with non-TCP payload (%u).\n", ip6->proto);
      return;
    }

    /* Store some relevant information about the packet. */

    pk.ip_ver = IP_VER6;

    pk.ip_opt_len = 0;

    memcpy(pk.src, ip6->src, 16);
    memcpy(pk.dst, ip6->dst, 16);

    pk.tos = (ver_tos >> 22) & 0x3F;

    pk.ttl = ip6->ttl;

    if (ver_tos & 0xFFFFF) pk.quirks |= QUIRK_FLOW;

    if ((ver_tos >> 20) & (IP_TOS_CE | IP_TOS_ECT)) pk.quirks |= QUIRK_ECN;

    pk.tot_hdr = sizeof(struct ipv6_hdr);

    tcp = (struct tcp_hdr*)(ip6 + 1);
    packet_len -= sizeof(struct ipv6_hdr);

  } else {

    if (!bad_packets) {
      WARN("Unknown packet type %u, link detection issue?", *data >> 4);
      bad_packets = 1;
    }

    return;

  }

  /***************
   * TCP parsing *
   ***************/

  data = (u8*)tcp;

  tcp_doff = (tcp->doff_rsvd >> 4) * 4;

  /* As usual, let's start with sanity checks. */

  if (tcp_doff < sizeof(struct tcp_hdr)) {
    DEBUG("[#] tcp.hdr_len = %u, not enough for TCP!\n", tcp_doff);
    return;
  }

  if (tcp_doff > packet_len) {
    DEBUG("[#] tcp.hdr_len = %u, past end of packet!\n", tcp_doff);
    return;
  }

  pk.tot_hdr += tcp_doff;

  pk.sport = ntohs(RD16(tcp->sport));
  pk.dport = ntohs(RD16(tcp->dport));

  pk.tcp_type = tcp->flags & (TCP_SYN | TCP_ACK | TCP_FIN | TCP_RST);

  /* NUL, SYN+FIN, SYN+RST, FIN+RST, etc, should go to /dev/null. */

  if (((tcp->flags & TCP_SYN) && (tcp->flags & (TCP_FIN | TCP_RST))) ||
      ((tcp->flags & TCP_FIN) && (tcp->flags & TCP_RST)) ||
      !pk.tcp_type) {

    DEBUG("[#] Silly combination of TCP flags: 0x%02x.\n", tcp->flags);
    return;

  }

  pk.win = ntohs(RD16(tcp->win));

  pk.seq = ntohl(RD32(tcp->seq));

  /* Take note of miscellanous features and quirks. */

  if ((tcp->flags & (TCP_ECE | TCP_CWR)) || 
      (tcp->doff_rsvd & TCP_NS_RES)) pk.quirks |= QUIRK_ECN;

  if (!pk.seq) pk.quirks |= QUIRK_ZERO_SEQ;

  if (tcp->flags & TCP_ACK) {

    if (!RD32(tcp->ack)) pk.quirks |= QUIRK_ZERO_ACK;

  } else {

    /* A good proportion of RSTs tend to have "illegal" ACK numbers, so
       ignore these. */

    if (RD32(tcp->ack) & !(tcp->flags & TCP_RST)) {

      DEBUG("[#] Non-zero ACK on a non-ACK packet: 0x%08x.\n",
            ntohl(RD32(tcp->ack)));

      pk.quirks |= QUIRK_NZ_ACK;

    }

  }

  if (tcp->flags & TCP_URG) {

    pk.quirks |= QUIRK_URG;

  } else {

    if (RD16(tcp->urg)) {

      DEBUG("[#] Non-zero UPtr on a non-URG packet: 0x%08x.\n",
            ntohl(RD16(tcp->urg)));

      pk.quirks |= QUIRK_NZ_URG;

    }

  }

  if (tcp->flags & TCP_PUSH) pk.quirks |= QUIRK_PUSH;

  /* Handle payload data. */

  if (tcp_doff == packet_len) {

    pk.payload = NULL;
    pk.pay_len = 0;

  } else {

    pk.payload = (u8*)data + tcp_doff;
    pk.pay_len = packet_len - tcp_doff;

  }

  /**********************
   * TCP option parsing *
   **********************/

  opt_end = (u8*)data + tcp_doff; /* First byte of non-option data */
  data = (u8*)(tcp + 1);

  pk.opt_cnt     = 0;
  pk.opt_eol_pad = 0;
  pk.mss         = 0;
  pk.wscale      = 0;
  pk.ts1         = 0;

  /* Option parsing problems are non-fatal, but we want to keep track of
     them to spot buggy TCP stacks. */

  while (data < opt_end && pk.opt_cnt < MAX_TCP_OPT) {

    pk.opt_layout[pk.opt_cnt++] = *data;

    switch (*data++) {

      case TCPOPT_EOL:

        /* EOL is a single-byte option that aborts further option parsing.
           Take note of how many bytes of option data are left, and if any of
           them are non-zero. */

        pk.opt_eol_pad = opt_end - data;
        
        while (data < opt_end && !*data++);

        if (data != opt_end) {
          pk.quirks |= QUIRK_OPT_EOL_NZ;
          data = opt_end;
        }

        break;

      case TCPOPT_NOP:

        /* NOP is a single-byte option that does nothing. */

        break;
  
      case TCPOPT_MAXSEG:

        /* MSS is a four-byte option with specified size. */

        if (data + 3 > opt_end) {
          DEBUG("[#] MSS option would end past end of header (%u left).\n",
                opt_end - data);
          goto abort_options;
        }

        if (*data != 4) {
          DEBUG("[#] MSS option expected to have 4 bytes, not %u.\n", *data);
          pk.quirks |= QUIRK_OPT_BAD;
        }

        pk.mss = ntohs(RD16p(data+1));

        data += 3;

        break;

      case TCPOPT_WSCALE:

        /* WS is a three-byte option with specified size. */

        if (data + 2 > opt_end) {
          DEBUG("[#] WS option would end past end of header (%u left).\n",
                opt_end - data);
          goto abort_options;
        }

        if (*data != 3) {
          DEBUG("[#] WS option expected to have 3 bytes, not %u.\n", *data);
          pk.quirks |= QUIRK_OPT_BAD;
        }

        pk.wscale = data[1];

        if (pk.wscale > 14) pk.quirks |= QUIRK_OPT_EXWS;

        data += 2;

        break;

      case TCPOPT_SACKOK:

        /* SACKOK is a two-byte option with specified size. */

        if (data + 1 > opt_end) {
          DEBUG("[#] SACKOK option would end past end of header (%u left).\n",
                opt_end - data);
          goto abort_options;
        }

        if (*data != 2) {
          DEBUG("[#] SACKOK option expected to have 2 bytes, not %u.\n", *data);
          pk.quirks |= QUIRK_OPT_BAD;
        }

        data++;

        break;

      case TCPOPT_SACK:

        /* SACK is a variable-length option of 10 to 34 bytes. Because we don't
           know the size any better, we need to bail out if it looks wonky. */

        if (data == opt_end) {
          DEBUG("[#] SACK option without room for length field.");
          goto abort_options;
        }

        if (*data < 10 || *data > 34) {
          DEBUG("[#] SACK length out of range (%u), bailing out.\n", *data);
          goto abort_options;
        }

        if (data - 1 + *data > opt_end) {
          DEBUG("[#] SACK option (len %u) is too long (%u left).\n",
                *data, opt_end - data);
          goto abort_options;
        }

        data += *data - 1;

        break;

      case TCPOPT_TSTAMP:

        /* Timestamp is a ten-byte option with specified size. */

        if (data + 9 > opt_end) {
          DEBUG("[#] TStamp option would end past end of header (%u left).\n",
                opt_end - data);
          goto abort_options;
        }

        if (*data != 10) {
          DEBUG("[#] TStamp option expected to have 10 bytes, not %u.\n",
                *data);
          pk.quirks |= QUIRK_OPT_BAD;
        }

        pk.ts1 = ntohl(RD32p(data + 1));

        if (!pk.ts1) pk.quirks |= QUIRK_OPT_ZERO_TS1;

        if (pk.tcp_type == TCP_SYN && RD32p(data + 5)) {

          DEBUG("[#] Non-zero second timestamp: 0x%08x.\n",
                ntohl(*(u32*)(data + 5)));

          pk.quirks |= QUIRK_OPT_NZ_TS2;

        }

        data += 9;

        break;

      default:

        /* Unknown option, presumably with specified size. */

        if (data == opt_end) {
          DEBUG("[#] Unknown option 0x%02x without room for length field.",
                data[-1]);
          goto abort_options;
        }

        if (*data < 2 || *data > 40) {
          DEBUG("[#] Unknown option 0x%02x has invalid length %u.\n",
                data[-1], *data);
          goto abort_options;
        }

        if (data - 1 + *data > opt_end) {
          DEBUG("[#] Unknown option 0x%02x (len %u) is too long (%u left).\n",
                data[-1], *data, opt_end - data);
          goto abort_options;
        }

        data += *data - 1;

    }

  }

  if (data != opt_end) {

abort_options:

    DEBUG("[#] Option parsing aborted (cnt = %u, remainder = %u).\n",
          pk.opt_cnt, opt_end - data);

    pk.quirks |= QUIRK_OPT_BAD;

  }

  flow_dispatch(&pk);

}


/* Calculate hash bucket for packet_flow. Keep the hash symmetrical: switching
   source and dest should have no effect. */

static u32 get_flow_bucket(struct packet_data* pk) {

  u32 bucket;

  if (pk->ip_ver == IP_VER4) {
    bucket = hash32(pk->src, 4, hash_seed) ^ hash32(pk->dst, 4, hash_seed);
  } else {
    bucket = hash32(pk->src, 16, hash_seed) ^ hash32(pk->dst, 16, hash_seed);
  }

  bucket ^= hash32(&pk->sport, 2, hash_seed) ^ hash32(&pk->dport, 2, hash_seed);

  return bucket % FLOW_BUCKETS;

}


/* Calculate hash bucket for host_data. */

static u32 get_host_bucket(u8* addr, u8 ip_ver) {

  u32 bucket;

  bucket = hash32(addr, (ip_ver == IP_VER4) ? 4 : 16, hash_seed);

  return bucket % HOST_BUCKETS;

}


/* Look up host data. */

struct host_data* lookup_host(u8* addr, u8 ip_ver) {

  u32 bucket = get_host_bucket(addr, ip_ver);
  struct host_data* h = host_b[bucket];

  while (CP(h)) {

    if (ip_ver == h->ip_ver &&
        !memcmp(addr, h->addr, (h->ip_ver == IP_VER4) ? 4 : 16))
      return h;

    h = h->next;

  }

  return NULL;

}


/* Destroy host data. */

static void destroy_host(struct host_data* h) {

  u32 bucket; 

  bucket = get_host_bucket(CP(h)->addr, h->ip_ver);

  if (h->use_cnt) FATAL("Attempt to destroy used host data.");

  DEBUG("[#] Destroying host data: %s (bucket %d)\n",
        addr_to_str(h->addr, h->ip_ver), bucket);

  /* Remove it from the bucketed linked list. */

  if (CP(h->next)) h->next->prev = h->prev;
  
  if (CP(h->prev)) h->prev->next = h->next;
  else host_b[bucket] = h->next;

  /* Remove from the by-age linked list. */

  if (CP(h->newer)) h->newer->older = h->older;
  else newest_host = h->older;

  if (CP(h->older)) h->older->newer = h->newer;
  else host_by_age = h->newer; 

  /* Free memory. */

  ck_free(h->last_syn);
  ck_free(h->last_synack);

  ck_free(h->http_resp);
  ck_free(h->http_req_os);

  ck_free(h);

  host_cnt--;

}


/* Indiscriminately kill some of the older hosts. */

static void nuke_hosts(void) {

  u32 kcnt = 1 + (host_cnt * KILL_PERCENT / 100);
  struct host_data* target = host_by_age;

  if (!read_file)
    WARN("Too many host entries, deleting %u. Use -m to adjust.", kcnt);

  nuke_flows(1);

  while (kcnt && CP(target)) {
    struct host_data* next = target->older;
    if (!target->use_cnt) { kcnt--; destroy_host(target); }
    target = next;
  }

}
  


/* Create a minimal host data. */

static struct host_data* create_host(u8* addr, u8 ip_ver) {

  u32 bucket = get_host_bucket(addr, ip_ver);
  struct host_data* nh;

  if (host_cnt > max_hosts) nuke_hosts();

  DEBUG("[#] Creating host data: %s (bucket %u)\n",
        addr_to_str(addr, ip_ver), bucket);

  nh = ck_alloc(sizeof(struct host_data));

  /* Insert into the bucketed linked list. */

  if (CP(host_b[bucket])) {
    host_b[bucket]->prev = nh;
    nh->next = host_b[bucket];
  }

  host_b[bucket] = nh;

  /* Insert into the by-age linked list. */
 
  if (CP(newest_host)) {

    newest_host->newer = nh;
    nh->older = newest_host;

  } else host_by_age = nh;

  newest_host = nh;

  /* Populate other data. */

  nh->ip_ver = ip_ver;
  memcpy(nh->addr, addr, (ip_ver == IP_VER4) ? 4 : 16);

  nh->last_seen = nh->first_seen = get_unix_time();

  nh->last_up_min     = -1;
  nh->last_class_id   = -1;
  nh->last_name_id    = -1;
  nh->http_name_id    = -1;
  nh->distance        = -1;

  host_cnt++;

  return nh;

}


/* Touch host data to make it more recent. */

static void touch_host(struct host_data* h) {

  CP(h);

  DEBUG("[#] Refreshing host data: %s\n", addr_to_str(h->addr, h->ip_ver));

  if (h != CP(newest_host)) {

    /* Remove from the the by-age linked list. */

    CP(h->newer);
    h->newer->older = h->older;

    if (CP(h->older)) h->older->newer = h->newer;
    else host_by_age = h->newer; 

    /* Re-insert in front. */

    newest_host->newer = h;
    h->older = newest_host;
    h->newer = NULL;

    newest_host = h;

    /* This wasn't the only entry on the list, so there is no
       need to update the tail (host_by_age). */

  }

  /* Update last seen time. */

  h->last_seen = get_unix_time();

}



/* Destroy a flow. */

static void destroy_flow(struct packet_flow* f) {

  CP(f);
  CP(f->client);
  CP(f->server);

  DEBUG("[#] Destroying flow: %s/%u -> ",
        addr_to_str(f->client->addr, f->client->ip_ver), f->cli_port);

  DEBUG("%s/%u (bucket %u)\n",
        addr_to_str(f->server->addr, f->server->ip_ver), f->srv_port,
        f->bucket);

  /* Remove it from the bucketed linked list. */

  if (CP(f->next)) f->next->prev = f->prev;
  
  if (CP(f->prev)) f->prev->next = f->next;
  else { CP(flow_b[f->bucket]); flow_b[f->bucket] = f->next; }

  /* Remove from the by-age linked list. */

  if (CP(f->newer)) f->newer->older = f->older;
  else { CP(newest_flow); newest_flow = f->older; }

  if (CP(f->older)) f->older->newer = f->newer;
  else flow_by_age = f->newer; 

  /* Free memory, etc. */

  f->client->use_cnt--;
  f->server->use_cnt--;

  free_sig_hdrs(&f->http_tmp);

  ck_free(f->request);
  ck_free(f->response);
  ck_free(f);

  flow_cnt--;  

}


/* Indiscriminately kill some of the oldest flows. */

static void nuke_flows(u8 silent) {

  u32 kcnt = 1 + (flow_cnt * KILL_PERCENT / 100);

  if (silent)
    DEBUG("[#] Pruning connections - trying to delete %u...\n",kcnt);
  else if (!read_file)
    WARN("Too many tracked connections, deleting %u. "
         "Use -m to adjust.", kcnt);

  while (kcnt-- && flow_by_age) destroy_flow(flow_by_age);

}



/* Create flow, and host data if necessary. If counts exceeded, prune old. */

static struct packet_flow* create_flow_from_syn(struct packet_data* pk) {

  u32 bucket = get_flow_bucket(pk);
  struct packet_flow* nf;

  if (flow_cnt > max_conn) nuke_flows(0);

  DEBUG("[#] Creating flow from SYN: %s/%u -> ",
        addr_to_str(pk->src, pk->ip_ver), pk->sport);

  DEBUG("%s/%u (bucket %u)\n",
        addr_to_str(pk->dst, pk->ip_ver), pk->dport, bucket);

  nf = ck_alloc(sizeof(struct packet_flow));

  nf->client = lookup_host(pk->src, pk->ip_ver);

  if (nf->client) touch_host(nf->client);
  else nf->client = create_host(pk->src, pk->ip_ver);

  nf->server = lookup_host(pk->dst, pk->ip_ver);

  if (nf->server) touch_host(nf->server);
  else nf->server = create_host(pk->dst, pk->ip_ver);

  nf->client->use_cnt++;
  nf->server->use_cnt++;

  nf->client->total_conn++;
  nf->server->total_conn++;

  /* Insert into the bucketed linked list.*/

  if (CP(flow_b[bucket])) {
    flow_b[bucket]->prev = nf;
    nf->next = flow_b[bucket];
  }

  flow_b[bucket] = nf;

  /* Insert into the by-age linked list */
 
  if (CP(newest_flow)) {
    newest_flow->newer = nf;
    nf->older = newest_flow;
  } else flow_by_age = nf;

  newest_flow = nf;

  /* Populate other data */

  nf->cli_port = pk->sport;
  nf->srv_port = pk->dport;
  nf->bucket   = bucket;
  nf->created  = get_unix_time();

  nf->next_cli_seq = pk->seq + 1;

  flow_cnt++;
  return nf;

}


/* Look up an existing flow. */

static struct packet_flow* lookup_flow(struct packet_data* pk, u8* to_srv) {

  u32 bucket = get_flow_bucket(pk);
  struct packet_flow* f = flow_b[bucket];

  while (CP(f)) {

    CP(f->client);
    CP(f->server);

    if (pk->ip_ver != f->client->ip_ver) goto lookup_next;

    if (pk->sport == f->cli_port && pk->dport == f->srv_port &&
        !memcmp(pk->src, f->client->addr, (pk->ip_ver == IP_VER4) ? 4 : 16) &&
        !memcmp(pk->dst, f->server->addr, (pk->ip_ver == IP_VER4) ? 4 : 16)) {

      *to_srv = 1;
      return f;

    }

    if (pk->dport == f->cli_port && pk->sport == f->srv_port &&
        !memcmp(pk->dst, f->client->addr, (pk->ip_ver == IP_VER4) ? 4 : 16) &&
        !memcmp(pk->src, f->server->addr, (pk->ip_ver == IP_VER4) ? 4 : 16)) {

      *to_srv = 0;
      return f;

    }

lookup_next:

    f = f->next;

  }

  return NULL;

}


/* Go through host and flow cache, expire outdated items. */

static void expire_cache(void) {
  struct host_data* target;
  static u32 pt;

  u32 ct = get_unix_time();

  if (ct == pt) return;
  pt = ct;

  DEBUG("[#] Cache expiration kicks in...\n");

  while (CP(flow_by_age) && ct - flow_by_age->created > conn_max_age)
    destroy_flow(flow_by_age);

  target = host_by_age;

  while (CP(target) && ct - target->last_seen > host_idle_limit * 60) {
    struct host_data* newer = target->newer;
    if (!target->use_cnt) destroy_host(target);
    target = newer;
  }

}


/* Insert data from a packet into a flow, call handlers as appropriate. */

static void flow_dispatch(struct packet_data* pk) {

  struct packet_flow* f;
  struct tcp_sig* tsig;
  u8 to_srv = 0;
  u8 need_more = 0;

  DEBUG("[#] Received TCP packet: %s/%u -> ",
        addr_to_str(pk->src, pk->ip_ver), pk->sport);

  DEBUG("%s/%u (type 0x%02x, pay_len = %u)\n",
        addr_to_str(pk->dst, pk->ip_ver), pk->dport, pk->tcp_type,
        pk->pay_len);
    
  f = lookup_flow(pk, &to_srv);

  switch (pk->tcp_type) {

    case TCP_SYN:

      if (f) {

        /* Perhaps just a simple dupe? */
        if (to_srv && f->next_cli_seq - 1 == pk->seq) return;

        DEBUG("[#] New SYN for an existing flow, resetting.\n");
        destroy_flow(f);

      }

      f = create_flow_from_syn(pk);

      tsig = fingerprint_tcp(1, pk, f);

      /* We don't want to do any further processing on generic non-OS
         signatures (e.g. NMap). The easiest way to guarantee that is to 
         kill the flow. */

      if (!tsig && !f->sendsyn) {

        destroy_flow(f);
        return;

      }

      fingerprint_mtu(1, pk, f);
      check_ts_tcp(1, pk, f);

      if (tsig) {

        /* This can't be done in fingerprint_tcp because check_ts_tcp()
           depends on having original SYN / SYN+ACK data. */
 
        ck_free(f->client->last_syn);
        f->client->last_syn = tsig;

      }

      break;

    case TCP_SYN | TCP_ACK:

      if (!f) {

        DEBUG("[#] Stray SYN+ACK with no flow.\n");
        return;

      }

      /* This is about as far as we want to go with p0f-sendsyn. */

      if (f->sendsyn) {

        fingerprint_tcp(0, pk, f);
        destroy_flow(f);
        return;

      }


      if (to_srv) {

        DEBUG("[#] SYN+ACK from client to server, trippy.\n");
        return;

      }

      if (f->acked) {

        if (f->next_srv_seq - 1 != pk->seq)
          DEBUG("[#] Repeated but non-identical SYN+ACK (0x%08x != 0x%08x).\n",
                f->next_srv_seq - 1, pk->seq);

        return;

      }

      f->acked = 1;

      tsig = fingerprint_tcp(0, pk, f);

      /* SYN from real OS, SYN+ACK from a client stack. Weird, but whatever. */

      if (!tsig) {

        destroy_flow(f);
        return;

      }

      fingerprint_mtu(0, pk, f);
      check_ts_tcp(0, pk, f);

      ck_free(f->server->last_synack);
      f->server->last_synack = tsig;

      f->next_srv_seq = pk->seq + 1;

      break;

    case TCP_RST | TCP_ACK:
    case TCP_RST:
    case TCP_FIN | TCP_ACK:
    case TCP_FIN:

       if (f) {

         check_ts_tcp(to_srv, pk, f);
         destroy_flow(f);

       }

       break;

    case TCP_ACK:

      if (!f) return;

      /* Stop there, you criminal scum! */

      if (f->sendsyn) {
        destroy_flow(f);
        return;
      }

      if (!f->acked) {

        DEBUG("[#] Never received SYN+ACK to complete handshake, huh.\n");
        destroy_flow(f);
        return;

      }

      if (to_srv) {

        /* We don't do stream reassembly, so if something arrives out of order,
           we won't catch it. Oh well. */

        if (f->next_cli_seq != pk->seq) {

          /* Not a simple dupe? */

          if (f->next_cli_seq - pk->pay_len != pk->seq)
            DEBUG("[#] Expected client seq 0x%08x, got 0x%08x.\n", f->next_cli_seq, pk->seq);
 
          return;
        }

        /* Append data */

        if (f->req_len < MAX_FLOW_DATA && pk->pay_len) {

          u32 read_amt = MIN(pk->pay_len, MAX_FLOW_DATA - f->req_len);

          f->request = ck_realloc_kb(f->request, f->req_len + read_amt + 1);
          memcpy(f->request + f->req_len, pk->payload, read_amt);
          f->req_len += read_amt;

        }

        check_ts_tcp(1, pk, f);

        f->next_cli_seq += pk->pay_len;

      } else {

        if (f->next_srv_seq != pk->seq) {

          /* Not a simple dupe? */

          if (f->next_srv_seq - pk->pay_len != pk->seq)
            DEBUG("[#] Expected server seq 0x%08x, got 0x%08x.\n",
                  f->next_cli_seq, pk->seq);
 
          return;

        }

        /* Append data */

        if (f->resp_len < MAX_FLOW_DATA && pk->pay_len) {

          u32 read_amt = MIN(pk->pay_len, MAX_FLOW_DATA - f->resp_len);

          f->response = ck_realloc_kb(f->response, f->resp_len + read_amt + 1);
          memcpy(f->response + f->resp_len, pk->payload, read_amt);
          f->resp_len += read_amt;

        }

        check_ts_tcp(0, pk, f);

        f->next_srv_seq += pk->pay_len;

      }

      if (!pk->pay_len) return;

      need_more |= process_http(to_srv, f);

      if (!need_more) {

        DEBUG("[#] All modules done, no need to keep tracking flow.\n");
        destroy_flow(f);

      } else if (f->req_len >= MAX_FLOW_DATA && f->resp_len >= MAX_FLOW_DATA) {

        DEBUG("[#] Per-flow capture size limit exceeded.\n");
        destroy_flow(f);

      }

      break;

    default:

      WARN("Huh. Unexpected packet type 0x%02x in flow_dispatch().", pk->tcp_type);

  }

}


/* Add NAT score, check if alarm due. */

void add_nat_score(u8 to_srv, struct packet_flow* f, u16 reason, u8 score) {

  static u8 rea[1024];

  struct host_data* hd;
  u8 *scores, *rptr = rea;
  u32 i;
  u8  over_5 = 0, over_2 = 0, over_1 = 0, over_0 = 0;

  if (to_srv) {

    hd = f->client;
    scores = hd->cli_scores;

  } else {

    hd = f->server;
    scores = hd->srv_scores;

  }

  memmove(scores, scores + 1, NAT_SCORES - 1);
  scores[NAT_SCORES - 1] = score;
  hd->nat_reasons |= reason;

  if (!score) return;

  for (i = 0; i < NAT_SCORES; i++) switch (scores[i]) {
    case 6 ... 255: over_5++;
    case 3 ... 5:   over_2++;
    case 2:         over_1++;
    case 1:         over_0++;
  }

  if (over_5 > 2 || over_2 > 4 || over_1 > 6 || over_0 > 8) {

    start_observation("ip sharing", 2, to_srv, f);

    reason = hd->nat_reasons;

    hd->last_nat = get_unix_time();

    memset(scores, 0, NAT_SCORES);
    hd->nat_reasons = 0;

  } else {

    /* Wait for something more substantial. */
    if (score == 1) return;

    start_observation("host change", 2, to_srv, f);

    hd->last_chg = get_unix_time();

  }

  *rptr = 0;

#define REAF(_par...) do { \
    rptr += sprintf((char*)rptr, _par); \
  } while (0) 

  if (reason & NAT_APP_SIG)  REAF(" app_vs_os");
  if (reason & NAT_OS_SIG)   REAF(" os_diff");
  if (reason & NAT_UNK_DIFF) REAF(" sig_diff");
  if (reason & NAT_TO_UNK)   REAF(" x_known");
  if (reason & NAT_TS)       REAF(" tstamp");
  if (reason & NAT_TTL)      REAF(" ttl");
  if (reason & NAT_PORT)     REAF(" port");
  if (reason & NAT_MSS)      REAF(" mtu");
  if (reason & NAT_FUZZY)    REAF(" fuzzy");

  if (reason & NAT_APP_VIA)  REAF(" via");
  if (reason & NAT_APP_DATE) REAF(" date");
  if (reason & NAT_APP_LB)   REAF(" srv_sig_lb");
  if (reason & NAT_APP_UA)   REAF(" ua_vs_os");

#undef REAF

  add_observation_field("reason", rea[0] ? (rea + 1) : NULL);

  OBSERVF("raw_hits", "%u,%u,%u,%u", over_5, over_2, over_1, over_0);

}


/* Verify if tool class (called from modules). */

void verify_tool_class(u8 to_srv, struct packet_flow* f, u32* sys, u32 sys_cnt) {

  struct host_data* hd;
  u32 i;

  if (to_srv) hd = f->client; else hd = f->server;

  CP(sys);

  /* No existing data; although there is perhaps some value in detecting
     app-only conflicts in absence of other info, it's probably OK to just
     wait until more data becomes available. */

  if (hd->last_class_id == -1) return;

  for (i = 0; i < sys_cnt; i++)

    if ((sys[i] & SYS_CLASS_FLAG)) {

      if (SYS_NF(sys[i]) == hd->last_class_id) break;

    } else {

      if (SYS_NF(sys[i]) == hd->last_name_id) break;

    }

  /* Oops, a mismatch. */

  if (i == sys_cnt) {

    DEBUG("[#] Detected app not supposed to run on host OS.\n");
    add_nat_score(to_srv, f, NAT_APP_SIG, 4);

  } else {

    DEBUG("[#] Detected app supported on host OS.\n");
    add_nat_score(to_srv, f, 0, 0);

  }

}


/* Clean up everything. */

void destroy_all_hosts(void) {

  while (flow_by_age) destroy_flow(flow_by_age);
  while (host_by_age) destroy_host(host_by_age);

}
