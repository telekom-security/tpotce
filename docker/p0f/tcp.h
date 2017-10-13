/*
   p0f - portable IP and TCP headers
   ---------------------------------

   Note that all multi-byte fields are in network (i.e., big) endian, and may
   need to be converted before use.

   Copyright (C) 2012 by Michal Zalewski <lcamtuf@coredump.cx>

   Distributed under the terms and conditions of GNU LGPL.

 */

#ifndef _HAVE_TCP_H
#define _HAVE_TCP_H

#include "types.h"

/*************
 * IP common *
 *************/

/* Protocol versions: */

#define IP_VER4           0x04
#define IP_VER6           0x06

/* IP-level ECN: */

#define IP_TOS_CE         0x01    /* Congestion encountered          */
#define IP_TOS_ECT        0x02    /* ECN supported                   */

/* Encapsulated protocols we care about: */

#define PROTO_TCP         0x06


/********
 * IPv4 *
 ********/

struct ipv4_hdr {

  u8  ver_hlen;          /* IP version (4), IP hdr len in dwords (4) */
  u8  tos_ecn;           /* ToS field (6), ECN flags (2)             */
  u16 tot_len;           /* Total packet length, in bytes            */
  u16 id;                /* IP ID                                    */
  u16 flags_off;         /* Flags (3), fragment offset (13)          */
  u8  ttl;               /* Time to live                             */
  u8  proto;             /* Next protocol                            */
  u16 cksum;             /* Header checksum                          */
  u8  src[4];            /* Source IP                                */
  u8  dst[4];            /* Destination IP                           */

  /* Dword-aligned options may follow. */

} __attribute__((packed));

/* IP flags: */

#define IP4_MBZ           0x8000  /* "Must be zero"                  */
#define IP4_DF            0x4000  /* Don't fragment (usually PMTUD)  */
#define IP4_MF            0x2000  /* More fragments coming           */


/********
 * IPv6 *
 ********/

struct ipv6_hdr {

  u32 ver_tos;           /* Version (4), ToS (6), ECN (2), flow (20) */
  u16 pay_len;           /* Total payload length, in bytes           */
  u8  proto;             /* Next protocol                            */
  u8  ttl;               /* Time to live                             */
  u8  src[16];           /* Source IP                                */
  u8  dst[16];           /* Destination IP                           */

  /* Dword-aligned options may follow if proto != PROTO_TCP and are
     included in total_length; but we won't be seeing such traffic due
     to BPF rules. */

} __attribute__((packed));



/*******
 * TCP *
 *******/

struct tcp_hdr {

  u16 sport;             /* Source port                              */
  u16 dport;             /* Destination port                         */
  u32 seq;               /* Sequence number                          */
  u32 ack;               /* Acknowledgment number                    */
  u8  doff_rsvd;         /* Data off dwords (4), rsvd (3), ECN (1)   */
  u8  flags;             /* Flags, including ECN                     */
  u16 win;               /* Window size                              */
  u16 cksum;             /* Header and payload checksum              */
  u16 urg;               /* "Urgent" pointer                         */

  /* Dword-aligned options may follow. */

} __attribute__((packed));


/* Normal flags: */

#define TCP_FIN           0x01
#define TCP_SYN           0x02
#define TCP_RST           0x04
#define TCP_PUSH          0x08
#define TCP_ACK           0x10
#define TCP_URG           0x20

/* ECN stuff: */

#define TCP_ECE           0x40    /* ECN supported (SYN) or detected */
#define TCP_CWR           0x80    /* ECE acknowledgment              */
#define TCP_NS_RES        0x01    /* ECE notification via TCP        */

/* Notable options: */

#define TCPOPT_EOL        0       /* End of options (1)              */
#define TCPOPT_NOP        1       /* No-op (1)                       */
#define TCPOPT_MAXSEG     2       /* Maximum segment size (4)        */
#define TCPOPT_WSCALE     3       /* Window scaling (3)              */
#define TCPOPT_SACKOK     4       /* Selective ACK permitted (2)     */
#define TCPOPT_SACK       5       /* Actual selective ACK (10-34)    */
#define TCPOPT_TSTAMP     8       /* Timestamp (10)                  */


/***************
 * Other stuff *
 ***************/

#define MIN_TCP4 (sizeof(struct ipv4_hdr) + sizeof(struct tcp_hdr))
#define MIN_TCP6 (sizeof(struct ipv6_hdr) + sizeof(struct tcp_hdr))

#endif /* !_HAVE_TCP_H */
