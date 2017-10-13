/*
   p0f - packet capture and overall host / flow bookkeeping
   --------------------------------------------------------

   Copyright (C) 2012 by Michal Zalewski <lcamtuf@coredump.cx>

   Distributed under the terms and conditions of GNU LGPL.

 */

#ifndef _HAVE_PROCESS_H
#define _HAVE_PROCESS_H

#include <pcap.h>

#include "types.h"
#include "fp_tcp.h"
#include "fp_http.h"

/* Parsed information handed over by the pcap callback: */

struct packet_data {

  u8  ip_ver;                           /* IP_VER4, IP_VER6                   */
  u8  tcp_type;                         /* TCP_SYN, ACK, FIN, RST             */

  u8  src[16];                          /* Source address (left-aligned)      */
  u8  dst[16];                          /* Destination address (left-aligned  */

  u16 sport;                            /* Source port                        */
  u16 dport;                            /* Destination port                   */

  u8  ttl;                              /* Observed TTL                       */
  u8  tos;                              /* IP ToS value                       */

  u16 mss;                              /* Maximum segment size               */
  u16 win;                              /* Window size                        */
  u8  wscale;                           /* Window scaling                     */
  u16 tot_hdr;                          /* Total headers (for MTU calc)       */

  u8  opt_layout[MAX_TCP_OPT];          /* Ordering of TCP options            */
  u8  opt_cnt;                          /* Count of TCP options               */
  u8  opt_eol_pad;                      /* Amount of padding past EOL         */

  u32 ts1;                              /* Own timestamp                      */

  u32 quirks;                           /* QUIRK_*                            */

  u8  ip_opt_len;                       /* Length of IP options               */

  u8* payload;                          /* TCP payload                        */
  u16 pay_len;                          /* Length of TCP payload              */

  u32 seq;                              /* seq value seen                     */

};

/* IP-level quirks: */

#define QUIRK_ECN            0x00000001 /* ECN supported                      */
#define QUIRK_DF             0x00000002 /* DF used (probably PMTUD)           */
#define QUIRK_NZ_ID          0x00000004 /* Non-zero IDs when DF set           */
#define QUIRK_ZERO_ID        0x00000008 /* Zero IDs when DF not set           */
#define QUIRK_NZ_MBZ         0x00000010 /* IP "must be zero" field isn't      */
#define QUIRK_FLOW           0x00000020 /* IPv6 flows used                    */

/* Core TCP quirks: */

#define QUIRK_ZERO_SEQ       0x00001000 /* SEQ is zero                        */
#define QUIRK_NZ_ACK         0x00002000 /* ACK non-zero when ACK flag not set */
#define QUIRK_ZERO_ACK       0x00004000 /* ACK is zero when ACK flag set      */
#define QUIRK_NZ_URG         0x00008000 /* URG non-zero when URG flag not set */
#define QUIRK_URG            0x00010000 /* URG flag set                       */
#define QUIRK_PUSH           0x00020000 /* PUSH flag on a control packet      */

/* TCP option quirks: */

#define QUIRK_OPT_ZERO_TS1   0x01000000 /* Own timestamp set to zero          */
#define QUIRK_OPT_NZ_TS2     0x02000000 /* Peer timestamp non-zero on SYN     */
#define QUIRK_OPT_EOL_NZ     0x04000000 /* Non-zero padding past EOL          */
#define QUIRK_OPT_EXWS       0x08000000 /* Excessive window scaling           */
#define QUIRK_OPT_BAD        0x10000000 /* Problem parsing TCP options        */

/* Host record with persistent fingerprinting data: */

struct host_data {

  struct host_data *prev, *next;        /* Linked lists                       */
  struct host_data *older, *newer;
  u32 use_cnt;                          /* Number of packet_flows attached    */

  u32 first_seen;                       /* Record created (unix time)         */
  u32 last_seen;                        /* Host last seen (unix time)         */
  u32 total_conn;                       /* Total number of connections ever   */

  u8 ip_ver;                            /* Address type                       */
  u8 addr[16];                          /* Host address data                  */

  struct tcp_sig* last_syn;             /* Sig of the most recent SYN         */
  struct tcp_sig* last_synack;          /* Sig of the most recent SYN+ACK     */

  s32 last_class_id;                    /* OS class ID (-1 = not found)       */
  s32 last_name_id;                     /* OS name ID (-1 = not found)        */
  u8* last_flavor;                      /* Last OS flavor                     */

  u8  last_quality;                     /* Generic or fuzzy match?            */

  u8* link_type;                        /* MTU-derived link type              */

  u8  cli_scores[NAT_SCORES];           /* Scoreboard for client NAT          */
  u8  srv_scores[NAT_SCORES];           /* Scoreboard for server NAT          */
  u16 nat_reasons;                      /* NAT complaints                     */

  u32 last_nat;                         /* Last NAT detection time            */
  u32 last_chg;                         /* Last OS change detection time      */

  u16 last_port;                        /* Source port on last SYN            */

  u8  distance;                         /* Last measured distance             */

  s32 last_up_min;                      /* Last computed uptime (-1 = none)   */
  u32 up_mod_days;                      /* Uptime modulo (days)               */

  /* HTTP business: */

  struct http_sig* http_req_os;         /* Last request, if class != -1       */
  struct http_sig* http_resp;           /* Last response                      */

  s32 http_name_id;                     /* Client name ID (-1 = not found)    */
  u8* http_flavor;                      /* Client flavor                      */

  u8* language;                         /* Detected language                  */

  u8  bad_sw;                           /* Used dishonest U-A or Server?      */

  u16 http_resp_port;                   /* Port on which response seen        */

};

/* Reasons for NAT detection: */

#define NAT_APP_SIG          0x0001     /* App signature <-> OS mismatch      */
#define NAT_OS_SIG           0x0002     /* OS detection mismatch              */
#define NAT_UNK_DIFF         0x0004     /* Current sig unknown, but different */
#define NAT_TO_UNK           0x0008     /* Sig changed from known to unknown  */
#define NAT_TS               0x0010     /* Timestamp goes back                */
#define NAT_PORT             0x0020     /* Source port goes back              */
#define NAT_TTL              0x0040     /* TTL changes unexpectedly           */
#define NAT_FUZZY            0x0080     /* Signature fuzziness changes        */
#define NAT_MSS              0x0100     /* MSS changes                        */

#define NAT_APP_LB           0x0200     /* Server signature changes           */
#define NAT_APP_VIA          0x0400     /* Via / X-Forwarded-For seen         */
#define NAT_APP_DATE         0x0800     /* Date changes in a weird way        */
#define NAT_APP_UA           0x1000     /* User-Agent OS inconsistency        */

/* TCP flow record, maintained until all fingerprinting modules are happy: */

struct packet_flow {

  struct packet_flow *prev, *next;      /* Linked lists                       */
  struct packet_flow *older, *newer;
  u32 bucket;                           /* Bucket this flow belongs to        */

  struct host_data* client;             /* Requesting client                  */
  struct host_data* server;             /* Target server                      */

  u16 cli_port;                         /* Client port                        */
  u16 srv_port;                         /* Server port                        */

  u8  acked;                            /* SYN+ACK received?                  */
  u8  sendsyn;                          /* Created by p0f-sendsyn?            */

  s16 srv_tps;                          /* Computed TS divisor (-1 = bad)     */ 
  s16 cli_tps;

  u8* request;                          /* Client-originating data            */
  u32 req_len;                          /* Captured data length               */
  u32 next_cli_seq;                     /* Next seq on cli -> srv packet      */

  u8* response;                         /* Server-originating data            */
  u32 resp_len;                         /* Captured data length               */
  u32 next_srv_seq;                     /* Next seq on srv -> cli packet      */
  u16 syn_mss;                          /* MSS on SYN packet                  */

  u32 created;                          /* Flow creation date (unix time)     */

  /* Application-level fingerprinting: */

  s8  in_http;                          /* 0 = tbd, 1 = yes, -1 = no          */

  u8  http_req_done;                    /* Done collecting req headers?       */
  u32 http_pos;                         /* Current parsing offset             */
  u8  http_gotresp1;                    /* Got initial line of a response?    */

  struct http_sig http_tmp;             /* Temporary signature                */

};

extern u64 packet_cnt;

void parse_packet(void* junk, const struct pcap_pkthdr* hdr, const u8* data);

u8* addr_to_str(u8* data, u8 ip_ver);

u64 get_unix_time_ms(void);
u32 get_unix_time(void);

void add_nat_score(u8 to_srv, struct packet_flow* f, u16 reason, u8 score);
void verify_tool_class(u8 to_srv, struct packet_flow* f, u32* sys, u32 sys_cnt);

struct host_data* lookup_host(u8* addr, u8 ip_ver);

void destroy_all_hosts(void);

#endif /* !_HAVE_PROCESS_H */
