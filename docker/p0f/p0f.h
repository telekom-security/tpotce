/*
   p0f - exports from the main routine
   -----------------------------------

   Copyright (C) 2012 by Michal Zalewski <lcamtuf@coredump.cx>

   Distributed under the terms and conditions of GNU LGPL.

 */

#ifndef _HAVE_P0F_H
#define _HAVE_P0F_H

#include "types.h"
#include "process.h"

extern u8  daemon_mode;
extern s32 link_type;
extern u32 max_conn, max_hosts, conn_max_age, host_idle_limit, hash_seed;
extern u8* read_file;

void start_observation(char* keyword, u8 field_cnt, u8 to_srv,
                       struct packet_flow* pf);

void add_observation_field(char* key, u8* value);

#define OBSERVF(_key, _fmt...) do { \
    u8* _val; \
    _val = alloc_printf(_fmt); \
    add_observation_field(_key, _val); \
    ck_free(_val); \
  } while (0)

#include "api.h"

struct api_client {

  s32 fd;                               /* -1 if slot free                    */

  struct p0f_api_query in_data;         /* Query recv buffer                  */
  u32 in_off;                           /* Query buffer offset                */

  struct p0f_api_response out_data;     /* Response transmit buffer           */
  u32 out_off;                          /* Response buffer offset             */

};

#endif /* !_HAVE_P0F_H */
