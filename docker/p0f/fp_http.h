/*
   p0f - HTTP fingerprinting
   -------------------------

   Copyright (C) 2012 by Michal Zalewski <lcamtuf@coredump.cx>

   Distributed under the terms and conditions of GNU LGPL.

 */

#ifndef _HAVE_FP_HTTP_H
#define _HAVE_FP_HTTP_H

#include "types.h"

/* A structure used for looking up various headers internally in fp_http.c: */

struct http_id {
  char* name;
  u32 id;
};

/* Another internal structure for UA -> OS maps: */

struct ua_map_record {
  u8* name;
  u32 id;
};

/* HTTP header field: */

struct http_hdr {
  s32  id;                              /* Lookup ID (-1 = none)              */
  u8*  name;                            /* Text name (NULL = use lookup ID)   */
  u8*  value;                           /* Value, if any                      */
  u8   optional;                        /* Optional header?                   */
};

/* Request / response signature collected from the wire: */

struct http_sig {

  s8  http_ver;                         /* HTTP version (-1 = any)            */

  struct http_hdr hdr[HTTP_MAX_HDRS];   /* Mandatory / discovered headers     */
  u32 hdr_cnt;

  u64 hdr_bloom4;                       /* Bloom filter for headers           */

  u32 miss[HTTP_MAX_HDRS];              /* Missing headers                    */
  u32 miss_cnt;

  u8* sw;                               /* Software string (U-A or Server)    */
  u8* lang;                             /* Accept-Language                    */
  u8* via;                              /* Via or X-Forwarded-For             */

  u32 date;                             /* Parsed 'Date'                      */
  u32 recv_date;                        /* Actual receipt date                */

  /* Information used for matching with p0f.fp: */

  struct http_sig_record* matched;      /* NULL = no match                    */
  u8  dishonest;                        /* "sw" looks forged?                 */

};

/* Record for a HTTP signature read from p0f.fp: */

struct http_sig_record {

  s32 class_id;                         /* OS class ID (-1 = user)            */
  s32 name_id;                          /* OS name ID                         */
  u8* flavor;                           /* Human-readable flavor string       */

  u32 label_id;                         /* Signature label ID                 */

  u32* sys;                             /* OS class / name IDs for user apps  */
  u32  sys_cnt;                         /* Length of sys                      */

  u32  line_no;                         /* Line number in p0f.fp              */

  u8 generic;                           /* Generic signature?                 */

  struct http_sig* sig;                 /* Actual signature data              */

};

/* Register new HTTP signature. */

struct packet_flow;

void http_parse_ua(u8* val, u32 line_no);

void http_register_sig(u8 to_srv, u8 generic, s32 sig_class, u32 sig_name,
                       u8* sig_flavor, u32 label_id, u32* sys, u32 sys_cnt,
                       u8* val, u32 line_no);

u8 process_http(u8 to_srv, struct packet_flow* f);

void free_sig_hdrs(struct http_sig* h);

void http_init(void);

#endif /* _HAVE_FP_HTTP_H */
