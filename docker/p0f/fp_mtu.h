/*
   p0f - MTU matching
   ------------------

   Copyright (C) 2012 by Michal Zalewski <lcamtuf@coredump.cx>

   Distributed under the terms and conditions of GNU LGPL.

 */

#ifndef _HAVE_FP_MTU_H
#define _HAVE_FP_MTU_H

#include "types.h"

/* Record for a TCP signature read from p0f.fp: */

struct mtu_sig_record {

  u8* name;
  u16 mtu;

};

#include "process.h"

struct packet_data;
struct packet_flow;

void mtu_register_sig(u8* name, u8* val, u32 line_no);

void fingerprint_mtu(u8 to_srv, struct packet_data* pk, struct packet_flow* f);

#endif /* _HAVE_FP_MTU_H */
