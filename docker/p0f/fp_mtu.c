/*
   p0f - MTU matching
   ------------------

   Copyright (C) 2012 by Michal Zalewski <lcamtuf@coredump.cx>

   Distributed under the terms and conditions of GNU LGPL.

 */

#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

#include <netinet/in.h>
#include <sys/types.h>
#include <ctype.h>

#include "types.h"
#include "config.h"
#include "debug.h"
#include "alloc-inl.h"
#include "process.h"
#include "readfp.h"
#include "p0f.h"
#include "tcp.h"

#include "fp_mtu.h"

static struct mtu_sig_record* sigs[SIG_BUCKETS];
static u32 sig_cnt[SIG_BUCKETS];


/* Register a new MTU signature. */

void mtu_register_sig(u8* name, u8* val, u32 line_no) {

  u8* nxt = val;
  s32 mtu;
  u32 bucket;

  while (isdigit(*nxt)) nxt++;

  if (nxt == val || *nxt) FATAL("Malformed MTU value in line %u.", line_no);

  mtu = atol((char*)val);

  if (mtu <= 0 || mtu > 65535) FATAL("Malformed MTU value in line %u.", line_no);

  bucket = mtu % SIG_BUCKETS;

  sigs[bucket] = DFL_ck_realloc(sigs[bucket], (sig_cnt[bucket] + 1) *
                                sizeof(struct mtu_sig_record));

  sigs[bucket][sig_cnt[bucket]].mtu = mtu;
  sigs[bucket][sig_cnt[bucket]].name = name;

  sig_cnt[bucket]++;

}



void fingerprint_mtu(u8 to_srv, struct packet_data* pk, struct packet_flow* f) {

  u32 bucket, i, mtu;

  if (!pk->mss || f->sendsyn) return;

  start_observation("mtu", 2, to_srv, f);

  if (pk->ip_ver == IP_VER4) mtu = pk->mss + MIN_TCP4;
  else mtu = pk->mss + MIN_TCP6;

  bucket = (mtu) % SIG_BUCKETS;

  for (i = 0; i < sig_cnt[bucket]; i++)
    if (sigs[bucket][i].mtu == mtu) break;

  if (i == sig_cnt[bucket]) add_observation_field("link", NULL);
  else {

    add_observation_field("link", sigs[bucket][i].name);

    if (to_srv) f->client->link_type = sigs[bucket][i].name;
    else f->server->link_type = sigs[bucket][i].name;

  }

  OBSERVF("raw_mtu", "%u", mtu);

}
