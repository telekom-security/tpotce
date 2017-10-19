/*
   p0f-sendsyn6 - IPv6 SYN sender
   ------------------------------

   This trivial utility sends 8 SYN packets to open ports on destination hosts,
   and lets you capture SYN+ACK signatures. The problem with SYN+ACK
   fingerprinting is that on some systems, the response varies depending on the
   use of window scaling, timestamps, or selective ACK in the initial SYN - so
   this utility is necessary to exercise all the code paths.
   
   Note that the IPv6 variant will not compile properly if you don't have
   IPv6-enabled libc; and will not work unless your kernel actually supports
   IPv6.

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

#include <netinet/in.h>
#include <arpa/inet.h>
#include <sys/types.h>
#include <sys/time.h>
#include <sys/socket.h>

#include "../types.h"
#include "../config.h"
#include "../alloc-inl.h"
#include "../debug.h"
#include "../tcp.h"


/* Do a basic IPv6 TCP checksum. */

static void tcp_cksum(u8* src, u8* dst, struct tcp_hdr* t, u8 opt_len) {

  u32 sum, i;
  u8* p;

  if (opt_len % 4) FATAL("Packet size not aligned to 4.");

  t->cksum = 0;

  sum = PROTO_TCP + sizeof(struct tcp_hdr) + opt_len;

  p = (u8*)t;

  for (i = 0; i < sizeof(struct tcp_hdr) + opt_len; i += 2, p += 2)
    sum += (*p << 8) + p[1];

  p = src;

  for (i = 0; i < 16; i += 2, p += 2) sum += (*p << 8) + p[1];

  p = dst;

  for (i = 0; i < 16; i += 2, p += 2) sum += (*p << 8) + p[1];

  t->cksum = htons(~(sum + (sum >> 16)));

}


/* Parse IPv6 address into a buffer. */

static void parse_addr(char* str, u8* ret) { 

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


#define W(_x) (_x) >> 8, (_x) & 0xff
#define D(_x) (_x) >> 24, ((_x) >> 16) & 0xff, ((_x) >> 8) & 0xff, (_x) & 0xff

#define EOL       TCPOPT_EOL
#define NOP       TCPOPT_NOP
#define MSS(_x)   TCPOPT_MAXSEG, 4, W(_x)
#define WS(_x)    TCPOPT_WSCALE, 3, (_x)
#define SOK       TCPOPT_SACKOK, 2
#define TS(_x,_y) TCPOPT_TSTAMP, 10, D(_x), D(_y)

/* There are virtually no OSes that do not send MSS. Support for RFC 1323
   and 2018 is not given, so we have to test various combinations here. */

static u8 opt_combos[8][24] = {

  { MSS(SPECIAL_MSS), NOP, EOL },                           /* 6  */

  { MSS(SPECIAL_MSS), SOK, NOP, EOL },                      /* 8  */

  { MSS(SPECIAL_MSS), WS(5), NOP, EOL },                    /* 9  */

  { MSS(SPECIAL_MSS), WS(5), SOK, NOP, EOL },               /* 12 */

  { MSS(SPECIAL_MSS), TS(1337, 0), NOP, EOL },              /* 17 */

  { MSS(SPECIAL_MSS), SOK, TS(1337, 0), NOP, EOL },         /* 19 */

  { MSS(SPECIAL_MSS), WS(5), TS(1337, 0), NOP, EOL },       /* 20 */

  { MSS(SPECIAL_MSS), WS(5), SOK,  TS(1337, 0), NOP, EOL }  /* 22 */

};

int main(int argc, char** argv) {

  static struct sockaddr_in6 sin;
  char one = 1;
  s32  sock;
  u32  i;

  static u8 work_buf[MIN_TCP6 + 24];

  struct ipv6_hdr* ip6 = (struct ipv6_hdr*)work_buf;
  struct tcp_hdr*  tcp = (struct tcp_hdr*)(ip6 + 1);
  u8 *opts = work_buf + MIN_TCP6;


  if (argc != 4) {
    ERRORF("Usage: p0f-sendsyn your_ip dst_ip port\n");
    exit(1);
  }

  parse_addr(argv[1], ip6->src);
  parse_addr(argv[2], ip6->dst);

  sock = socket(AF_INET, SOCK_RAW, IPPROTO_IPV6);

  if (sock < 0) PFATAL("Can't open raw socket (you need to be root).");

  if (setsockopt(sock, IPPROTO_IP, IP_HDRINCL, (char*)&one, sizeof(char)))
    PFATAL("setsockopt() on raw socket failed.");

  sin.sin6_family = PF_INET6;
  
  memcpy(&sin.sin6_addr, ip6->dst, 16);

  ip6->ver_tos = ntohl(6 << 24);
  ip6->pay_len = ntohs(sizeof(struct tcp_hdr) + 24);
  ip6->proto   = PROTO_TCP;
  ip6->ttl     = 192;

  tcp->dport     = htons(atoi(argv[3]));
  tcp->seq       = htonl(0x12345678);
  tcp->doff_rsvd = ((sizeof(struct tcp_hdr) + 24) / 4) << 4;
  tcp->flags     = TCP_SYN;
  tcp->win       = htons(SPECIAL_WIN);

  for (i = 0; i < 8; i++) {

    tcp->sport = htons(65535 - i);

    memcpy(opts, opt_combos[i], 24);
    tcp_cksum(ip6->src, ip6->dst, tcp, 24);

    if (sendto(sock, work_buf, sizeof(work_buf), 0, (struct sockaddr*)&sin,
        sizeof(struct sockaddr_in6)) < 0) PFATAL("sendto() fails.");

    usleep(100000);

  }

  SAYF("Eight packets sent! Check p0f output to examine responses, if any.\n");

  return 0;

}

