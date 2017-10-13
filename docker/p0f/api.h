/*
   p0f - API query code
   --------------------

   Copyright (C) 2012 by Michal Zalewski <lcamtuf@coredump.cx>

   Distributed under the terms and conditions of GNU LGPL.

 */

#ifndef _HAVE_API_H
#define _HAVE_API_H

#include "types.h"

#define P0F_QUERY_MAGIC      0x50304601
#define P0F_RESP_MAGIC       0x50304602

#define P0F_STATUS_BADQUERY  0x00
#define P0F_STATUS_OK        0x10
#define P0F_STATUS_NOMATCH   0x20

#define P0F_ADDR_IPV4        0x04
#define P0F_ADDR_IPV6        0x06

#define P0F_STR_MAX          31

#define P0F_MATCH_FUZZY      0x01
#define P0F_MATCH_GENERIC    0x02

/* Keep these structures aligned to avoid architecture-specific padding. */

struct p0f_api_query {

  u32 magic;                            /* Must be P0F_QUERY_MAGIC            */
  u8  addr_type;                        /* P0F_ADDR_*                         */
  u8  addr[16];                         /* IP address (big endian left align) */

} __attribute__((packed));

struct p0f_api_response {

  u32 magic;                            /* Must be P0F_RESP_MAGIC             */
  u32 status;                           /* P0F_STATUS_*                       */

  u32 first_seen;                       /* First seen (unix time)             */
  u32 last_seen;                        /* Last seen (unix time)              */
  u32 total_conn;                       /* Total connections seen             */

  u32 uptime_min;                       /* Last uptime (minutes)              */
  u32 up_mod_days;                      /* Uptime modulo (days)               */

  u32 last_nat;                         /* NAT / LB last detected (unix time) */
  u32 last_chg;                         /* OS chg last detected (unix time)   */

  s16 distance;                         /* System distance                    */

  u8  bad_sw;                           /* Host is lying about U-A / Server   */
  u8  os_match_q;                       /* Match quality                      */

  u8  os_name[P0F_STR_MAX + 1];         /* Name of detected OS                */
  u8  os_flavor[P0F_STR_MAX + 1];       /* Flavor of detected OS              */

  u8  http_name[P0F_STR_MAX + 1];       /* Name of detected HTTP app          */
  u8  http_flavor[P0F_STR_MAX + 1];     /* Flavor of detected HTTP app        */

  u8  link_type[P0F_STR_MAX + 1];       /* Link type                          */

  u8  language[P0F_STR_MAX + 1];        /* Language                           */

} __attribute__((packed));

#ifdef _FROM_P0F

void handle_query(struct p0f_api_query* q, struct p0f_api_response* r);

#endif /* _FROM_API */

#endif /* !_HAVE_API_H */
