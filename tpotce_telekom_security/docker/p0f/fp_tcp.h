/*
   p0f - TCP/IP packet matching
   ----------------------------

   Copyright (C) 2012 by Michal Zalewski <lcamtuf@coredump.cx>

   Distributed under the terms and conditions of GNU LGPL.

 */

#ifndef _HAVE_FP_TCP_H
#define _HAVE_FP_TCP_H

#include "types.h"

/* Simplified data for signature matching and NAT detection: */

struct tcp_sig {

  u32 opt_hash;                         /* Hash of opt_layout & opt_cnt       */
  u32 quirks;                           /* Quirks                             */

  u8  opt_eol_pad;                      /* Amount of padding past EOL         */
  u8  ip_opt_len;                       /* Length of IP options               */

  s8  ip_ver;                           /* -1 = any, IP_VER4, IP_VER6         */

  u8  ttl;                              /* Actual TTL                         */

  s32 mss;                              /* Maximum segment size (-1 = any)    */
  u16 win;                              /* Window size                        */
  u8  win_type;                         /* WIN_TYPE_*                         */
  s16 wscale;                           /* Window scale (-1 = any)            */

  s8  pay_class;                        /* -1 = any, 0 = zero, 1 = non-zero   */

  u16 tot_hdr;                          /* Total header length                */
  u32 ts1;                              /* Own timestamp                      */
  u64 recv_ms;                          /* Packet recv unix time (ms)         */

  /* Information used for matching with p0f.fp: */

  struct tcp_sig_record* matched;       /* NULL = no match                    */
  u8  fuzzy;                            /* Approximate match?                 */
  u8  dist;                             /* Distance                           */

};

/* Methods for matching window size in tcp_sig: */

#define WIN_TYPE_NORMAL      0x00       /* Literal value                      */
#define WIN_TYPE_ANY         0x01       /* Wildcard (p0f.fp sigs only)        */
#define WIN_TYPE_MOD         0x02       /* Modulo check (p0f.fp sigs only)    */
#define WIN_TYPE_MSS         0x03       /* Window size MSS multiplier         */
#define WIN_TYPE_MTU         0x04       /* Window size MTU multiplier         */

/* Record for a TCP signature read from p0f.fp: */

struct tcp_sig_record {

  u8  generic;                          /* Generic entry?                     */
  s32 class_id;                         /* OS class ID (-1 = user)            */
  s32 name_id;                          /* OS name ID                         */
  u8* flavor;                           /* Human-readable flavor string       */

  u32 label_id;                         /* Signature label ID                 */

  u32* sys;                             /* OS class / name IDs for user apps  */
  u32  sys_cnt;                         /* Length of sys                      */

  u32  line_no;                         /* Line number in p0f.fp              */

  u8  bad_ttl;                          /* TTL is generated randomly          */

  struct tcp_sig* sig;                  /* Actual signature data              */

};

#include "process.h"

struct packet_data;
struct packet_flow;

void tcp_register_sig(u8 to_srv, u8 generic, s32 sig_class, u32 sig_name,
                      u8* sig_flavor, u32 label_id, u32* sys, u32 sys_cnt,
                      u8* val, u32 line_no);

struct tcp_sig* fingerprint_tcp(u8 to_srv, struct packet_data* pk,
                                struct packet_flow* f);

void fingerprint_sendsyn(struct packet_data* pk);

void check_ts_tcp(u8 to_srv, struct packet_data* pk, struct packet_flow* f);

#endif /* _HAVE_FP_TCP_H */
