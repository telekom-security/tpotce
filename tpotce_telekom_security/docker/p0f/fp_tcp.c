/*
   p0f - TCP/IP packet matching
   ----------------------------

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
#include "hash.h"
#include "tcp.h"
#include "readfp.h"
#include "p0f.h"

#include "fp_tcp.h"

/* TCP signature buckets: */

static struct tcp_sig_record* sigs[2][SIG_BUCKETS];
static u32 sig_cnt[2][SIG_BUCKETS];


/* Figure out what the TTL distance might have been for an unknown sig. */

static u8 guess_dist(u8 ttl) {
  if (ttl <= 32) return 32 - ttl;
  if (ttl <= 64) return 64 - ttl;
  if (ttl <= 128) return 128 - ttl;
  return 255 - ttl;
}


/* Figure out if window size is a multiplier of MSS or MTU. We don't take window
   scaling into account, because neither do TCP stack developers. */

static s16 detect_win_multi(struct tcp_sig* ts, u8* use_mtu, u16 syn_mss) {

  u16 win = ts->win;
  s32 mss = ts->mss, mss12 = mss - 12;

  if (!win || mss < 100 || ts->win_type != WIN_TYPE_NORMAL)
    return -1;

#define RET_IF_DIV(_div, _use_mtu, _desc) do { \
    if ((_div) && !(win % (_div))) { \
      *use_mtu = (_use_mtu); \
      DEBUG("[#] Window size %u is a multiple of %s [%u].\n", win, _desc, _div); \
      return win / (_div); \
    } \
  } while (0)

  RET_IF_DIV(mss, 0, "MSS");

  /* Some systems will sometimes subtract 12 bytes when timestamps are in use. */

  if (ts->ts1) RET_IF_DIV(mss12, 0, "MSS - 12");

  /* Some systems use MTU on the wrong interface, so let's check for the most
     common case. */

  RET_IF_DIV(1500 - MIN_TCP4, 0, "MSS (MTU = 1500, IPv4)");
  RET_IF_DIV(1500 - MIN_TCP4 - 12, 0, "MSS (MTU = 1500, IPv4 - 12)");

  if (ts->ip_ver == IP_VER6) {

    RET_IF_DIV(1500 - MIN_TCP6, 0, "MSS (MTU = 1500, IPv6)");
    RET_IF_DIV(1500 - MIN_TCP6 - 12, 0, "MSS (MTU = 1500, IPv6 - 12)");

  }

  /* Some systems use MTU instead of MSS: */

  RET_IF_DIV(mss + MIN_TCP4, 1, "MTU (IPv4)");
  RET_IF_DIV(mss + ts->tot_hdr, 1, "MTU (actual size)");
  if (ts->ip_ver == IP_VER6) RET_IF_DIV(mss + MIN_TCP6, 1, "MTU (IPv6)");
  RET_IF_DIV(1500, 1, "MTU (1500)");

  /* On SYN+ACKs, some systems use of the peer: */

  if (syn_mss) {

    RET_IF_DIV(syn_mss, 0, "peer MSS");
    RET_IF_DIV(syn_mss - 12, 0, "peer MSS - 12");

  }

#undef RET_IF_DIV

  return -1;

}



/* See if any of the p0f.fp signatures matches the collected data. */

static void tcp_find_match(u8 to_srv, struct tcp_sig* ts, u8 dupe_det,
                           u16 syn_mss) {

  struct tcp_sig_record* fmatch = NULL;
  struct tcp_sig_record* gmatch = NULL;

  u32 bucket = ts->opt_hash % SIG_BUCKETS;
  u32 i;

  u8  use_mtu = 0;
  s16 win_multi = detect_win_multi(ts, &use_mtu, syn_mss);

  CP(sigs[to_srv][bucket]);

  for (i = 0; i < sig_cnt[to_srv][bucket]; i++) {

    struct tcp_sig_record* ref = sigs[to_srv][bucket] + i;
    struct tcp_sig* refs = CP(ref->sig);

    u8 fuzzy = 0;
    u32 ref_quirks = refs->quirks;

    if (ref->sig->opt_hash != ts->opt_hash) continue;

    /* If the p0f.fp signature has no IP version specified, we need
       to remove IPv6-specific quirks from it when matching IPv4
       packets, and vice versa. */

    if (refs->ip_ver == -1)
       ref_quirks &= ((ts->ip_ver == IP_VER4) ? ~(QUIRK_FLOW) :
        ~(QUIRK_DF | QUIRK_NZ_ID | QUIRK_ZERO_ID));

    if (ref_quirks != ts->quirks) {

      u32 deleted = (ref_quirks ^ ts->quirks) & ref_quirks,
          added = (ref_quirks ^ ts->quirks) & ts->quirks;

      /* If there is a difference in quirks, but it amounts to 'df' or 'id+'
         disappearing, or 'id-' or 'ecn' appearing, allow a fuzzy match. */

      if (fmatch || (deleted & ~(QUIRK_DF | QUIRK_NZ_ID)) ||
          (added & ~(QUIRK_ZERO_ID | QUIRK_ECN))) continue;

      fuzzy = 1;

    }

    /* Fixed parameters. */

    if (refs->opt_eol_pad != ts->opt_eol_pad ||
        refs->ip_opt_len != ts->ip_opt_len) continue;

    /* TTL matching, with a provision to allow fuzzy match. */

    if (ref->bad_ttl) {

      if (refs->ttl < ts->ttl) continue;

    } else {

      if (refs->ttl < ts->ttl || refs->ttl - ts->ttl > MAX_DIST) fuzzy = 1;

    }

    /* Simple wildcards. */

    if (refs->mss != -1 && refs->mss != ts->mss) continue;
    if (refs->wscale != -1 && refs->wscale != ts->wscale) continue;
    if (refs->pay_class != -1 && refs->pay_class != ts->pay_class) continue;

    /* Window size. */

    if (ts->win_type != WIN_TYPE_NORMAL) {

      /* Comparing two p0f.fp signatures. */

      if (refs->win_type != ts->win_type || refs->win != ts->win) continue;

    } else {

      /* Comparing real-world stuff. */

      switch (refs->win_type) {

        case WIN_TYPE_NORMAL:
      
          if (refs->win != ts->win) continue;
          break;

        case WIN_TYPE_MOD:
      
          if (ts->win % refs->win) continue;
          break;

        case WIN_TYPE_MSS:

          if (use_mtu || refs->win != win_multi) continue;
          break;

        case WIN_TYPE_MTU:

          if (!use_mtu || refs->win != win_multi) continue;
          break;

        /* WIN_TYPE_ANY */

      }

    }

    /* Got a match? If not fuzzy, return. If fuzzy, keep looking. */

    if (!fuzzy) {

      if (!ref->generic) {

        ts->matched = ref;
        ts->fuzzy   = 0;
        ts->dist    = refs->ttl - ts->ttl;
        return;

      } else if (!gmatch) gmatch = ref;

    } else if (!fmatch) fmatch = ref;

  }

  /* OK, no definitive match so far... */

  if (dupe_det) return;

  /* If we found a generic signature, and nothing better, let's just use
     that. */

  if (gmatch) {

    ts->matched = gmatch;
    ts->fuzzy   = 0;
    ts->dist    = gmatch->sig->ttl - ts->ttl;
    return;

  }

  /* No fuzzy matching for userland tools. */

  if (fmatch && fmatch->class_id == -1) return;

  /* Let's try to guess distance if no match; or if match TTL out of
     range. */

  if (!fmatch || fmatch->sig->ttl < ts->ttl ||
       (!fmatch->bad_ttl && fmatch->sig->ttl - ts->ttl > MAX_DIST))
    ts->dist = guess_dist(ts->ttl);
  else
    ts->dist = fmatch->sig->ttl - ts->ttl;

  /* Record the outcome. */

  ts->matched = fmatch;

  if (fmatch) ts->fuzzy = 1;
  
}


/* Parse TCP-specific bits and register a signature read from p0f.fp. This
   function is too long. */

void tcp_register_sig(u8 to_srv, u8 generic, s32 sig_class, u32 sig_name,
                      u8* sig_flavor, u32 label_id, u32* sys, u32 sys_cnt,
                      u8* val, u32 line_no) {

  s8  ver, win_type, pay_class;
  u8  opt_layout[MAX_TCP_OPT];
  u8  opt_cnt = 0, bad_ttl = 0;

  s32 ittl, olen, mss, win, scale, opt_eol_pad = 0;
  u32 quirks = 0, bucket, opt_hash;

  u8* nxt;

  struct tcp_sig* tsig;
  struct tcp_sig_record* trec;

  /* IP version */

  switch (*val) {
    case '4': ver = IP_VER4; break;
    case '6': ver = IP_VER6; break;
    case '*': ver = -1; break;
    default: FATAL("Unrecognized IP version in line %u.", line_no);
  }

  if (val[1] != ':') FATAL("Malformed signature in line %u.", line_no);

  val += 2;

  /* Initial TTL (possibly ttl+dist or ttl-) */

  nxt = val;
  while (isdigit(*nxt)) nxt++;

  if (*nxt != ':' && *nxt != '+' && *nxt != '-')
    FATAL("Malformed signature in line %u.", line_no);

  ittl = atol((char*)val);
  if (ittl < 1 || ittl > 255) FATAL("Bogus initial TTL in line %u.", line_no);
  val = nxt + 1;

  if (*nxt == '-' && nxt[1] == ':') {

    bad_ttl = 1;
    val += 2;

  } else if (*nxt == '+') {

    s32 ittl_add;

    nxt++;
    while (isdigit(*nxt)) nxt++;
    if (*nxt != ':') FATAL("Malformed signature in line %u.", line_no);

    ittl_add = atol((char*)val);

    if (ittl_add < 0 || ittl + ittl_add > 255)
      FATAL("Bogus initial TTL in line %u.", line_no);

    ittl += ittl_add;
    val = nxt + 1;

  }

  /* Length of IP options */

  nxt = val;
  while (isdigit(*nxt)) nxt++;
  if (*nxt != ':') FATAL("Malformed signature in line %u.", line_no);

  olen = atol((char*)val);
  if (olen < 0 || olen > 255)
    FATAL("Bogus IP option length in line %u.", line_no);

  val = nxt + 1;

  /* MSS */

  if (*val == '*' && val[1] == ':') {

    mss = -1;
    val += 2;

  } else {

    nxt = val;
    while (isdigit(*nxt)) nxt++;
    if (*nxt != ':') FATAL("Malformed signature in line %u.", line_no);

    mss = atol((char*)val);
    if (mss < 0 || mss > 65535) FATAL("Bogus MSS in line %u.", line_no);
    val = nxt + 1;

  }

  /* window size, followed by comma */

  if (*val == '*' && val[1] == ',') {

    win_type = WIN_TYPE_ANY;
    win = 0;
    val += 2;

  } else if (*val == '%') {

    win_type = WIN_TYPE_MOD;

    val++;

    nxt = val;
    while (isdigit(*nxt)) nxt++;
    if (*nxt != ',') FATAL("Malformed signature in line %u.", line_no);

    win = atol((char*)val);
    if (win < 2 || win > 65535) FATAL("Bogus '%%' value in line %u.", line_no);
    val = nxt + 1;

  } else if (!strncmp((char*)val, "mss*", 4) ||
             !strncmp((char*)val, "mtu*", 4)) {

    win_type = (val[1] == 's') ? WIN_TYPE_MSS : WIN_TYPE_MTU;

    val += 4;

    nxt = val;
    while (isdigit(*nxt)) nxt++;
    if (*nxt != ',') FATAL("Malformed signature in line %u.", line_no);

    win = atol((char*)val);
    if (win < 1 || win > 1000)
      FATAL("Bogus MSS/MTU multiplier in line %u.", line_no);

    val = nxt + 1;

  } else {

    win_type = WIN_TYPE_NORMAL;

    nxt = val;
    while (isdigit(*nxt)) nxt++;
    if (*nxt != ',') FATAL("Malformed signature in line %u.", line_no);

    win = atol((char*)val);
    if (win < 0 || win > 65535) FATAL("Bogus window size in line %u.", line_no);
    val = nxt + 1;

  }

  /* Window scale */

  if (*val == '*' && val[1] == ':') {

    scale = -1;
    val += 2;

  } else {

    nxt = val;
    while (isdigit(*nxt)) nxt++;
    if (*nxt != ':') FATAL("Malformed signature in line %u.", line_no);

    scale = atol((char*)val);
    if (scale < 0 || scale > 255)
      FATAL("Bogus window scale in line %u.", line_no);

    val = nxt + 1;

  }

  /* Option layout */

  memset(opt_layout, 0, sizeof(opt_layout));  

  while (*val != ':') {

    if (opt_cnt >= MAX_TCP_OPT)
      FATAL("Too many TCP options in line %u.", line_no);

    if (!strncmp((char*)val, "eol", 3)) {

      opt_layout[opt_cnt++] = TCPOPT_EOL;
      val += 3;

      if (*val != '+')
        FATAL("Malformed EOL option in line %u.", line_no);

      val++;
      nxt = val;
      while (isdigit(*nxt)) nxt++;

      if (!*nxt) FATAL("Truncated options in line %u.", line_no);

      if (*nxt != ':')
        FATAL("EOL must be the last option in line %u.", line_no);

      opt_eol_pad = atol((char*)val);

      if (opt_eol_pad < 0 || opt_eol_pad > 255)
        FATAL("Bogus EOL padding in line %u.", line_no);

      val = nxt;

    } else if (!strncmp((char*)val, "nop", 3)) {

      opt_layout[opt_cnt++] = TCPOPT_NOP;
      val += 3;

    } else if (!strncmp((char*)val, "mss", 3)) {

      opt_layout[opt_cnt++] = TCPOPT_MAXSEG;
      val += 3;

    } else if (!strncmp((char*)val, "ws", 2)) {

      opt_layout[opt_cnt++] = TCPOPT_WSCALE;
      val += 2;

    } else if (!strncmp((char*)val, "sok", 3)) {

      opt_layout[opt_cnt++] = TCPOPT_SACKOK;
      val += 3;

    } else if (!strncmp((char*)val, "sack", 4)) {

      opt_layout[opt_cnt++] = TCPOPT_SACK;
      val += 4;

    } else if (!strncmp((char*)val, "ts", 2)) {

      opt_layout[opt_cnt++] = TCPOPT_TSTAMP;
      val += 2;

    } else if (*val == '?') {

      s32 optno;

      val++;
      nxt = val;
      while (isdigit(*nxt)) nxt++;

      if (*nxt != ':' && *nxt != ',')
        FATAL("Malformed '?' option in line %u.", line_no);

      optno = atol((char*)val);

      if (optno < 0 || optno > 255)
          FATAL("Bogus '?' option in line %u.", line_no);

      opt_layout[opt_cnt++] = optno;

      val = nxt;

    } else {

      FATAL("Unrecognized TCP option in line %u.", line_no);

    }

    if (*val == ':') break;

    if (*val != ',')
      FATAL("Malformed TCP options in line %u.", line_no);

    val++;

  }

  val++;

  opt_hash = hash32(opt_layout, opt_cnt, hash_seed);

  /* Quirks */

  while (*val != ':') {

    if (!strncmp((char*)val, "df", 2)) {

      if (ver == IP_VER6)
        FATAL("'df' is not valid for IPv6 in line %d.", line_no);

      quirks |= QUIRK_DF;
      val += 2;

    } else if (!strncmp((char*)val, "id+", 3)) {

      if (ver == IP_VER6)
        FATAL("'id+' is not valid for IPv6 in line %d.", line_no);

      quirks |= QUIRK_NZ_ID;
      val += 3;

    } else if (!strncmp((char*)val, "id-", 3)) {

      if (ver == IP_VER6)
        FATAL("'id-' is not valid for IPv6 in line %d.", line_no);

      quirks |= QUIRK_ZERO_ID;
      val += 3;

    } else if (!strncmp((char*)val, "ecn", 3)) {

      quirks |= QUIRK_ECN;
      val += 3;

    } else if (!strncmp((char*)val, "0+", 2)) {

      if (ver == IP_VER6)
        FATAL("'0+' is not valid for IPv6 in line %d.", line_no);

      quirks |= QUIRK_NZ_MBZ;
      val += 2;

    } else if (!strncmp((char*)val, "flow", 4)) {

      if (ver == IP_VER4)
        FATAL("'flow' is not valid for IPv4 in line %d.", line_no);

      quirks |= QUIRK_FLOW;
      val += 4;

    } else if (!strncmp((char*)val, "seq-", 4)) {

      quirks |= QUIRK_ZERO_SEQ;
      val += 4;

    } else if (!strncmp((char*)val, "ack+", 4)) {

      quirks |= QUIRK_NZ_ACK;
      val += 4;

    } else if (!strncmp((char*)val, "ack-", 4)) {

      quirks |= QUIRK_ZERO_ACK;
      val += 4;

    } else if (!strncmp((char*)val, "uptr+", 5)) {

      quirks |= QUIRK_NZ_URG;
      val += 5;

    } else if (!strncmp((char*)val, "urgf+", 5)) {

      quirks |= QUIRK_URG;
      val += 5;

    } else if (!strncmp((char*)val, "pushf+", 6)) {

      quirks |= QUIRK_PUSH;
      val += 6;

    } else if (!strncmp((char*)val, "ts1-", 4)) {

      quirks |= QUIRK_OPT_ZERO_TS1;
      val += 4;

    } else if (!strncmp((char*)val, "ts2+", 4)) {

      quirks |= QUIRK_OPT_NZ_TS2;
      val += 4;

    } else if (!strncmp((char*)val, "opt+", 4)) {

      quirks |= QUIRK_OPT_EOL_NZ;
      val += 4;

    } else if (!strncmp((char*)val, "exws", 4)) {

      quirks |= QUIRK_OPT_EXWS;
      val += 4;

    } else if (!strncmp((char*)val, "bad", 3)) {

      quirks |= QUIRK_OPT_BAD;
      val += 3;

    } else {

      FATAL("Unrecognized quirk in line %u.", line_no);

    }

    if (*val == ':') break;

    if (*val != ',')
      FATAL("Malformed quirks in line %u.", line_no);

    val++;

  }

  val++;

  /* Payload class */

  if (!strcmp((char*)val, "*")) pay_class = -1;
  else if (!strcmp((char*)val, "0")) pay_class = 0;
  else if (!strcmp((char*)val, "+")) pay_class = 1;
  else FATAL("Malformed payload class in line %u.", line_no);

  /* Phew, okay, we're done. Now, create tcp_sig... */

  tsig = DFL_ck_alloc(sizeof(struct tcp_sig));

  tsig->opt_hash    = opt_hash;
  tsig->opt_eol_pad = opt_eol_pad;

  tsig->quirks      = quirks;

  tsig->ip_opt_len  = olen;
  tsig->ip_ver      = ver;
  tsig->ttl         = ittl;

  tsig->mss         = mss;
  tsig->win         = win;
  tsig->win_type    = win_type;
  tsig->wscale      = scale;
  tsig->pay_class   = pay_class;

  /* No need to set ts1, recv_ms, match, fuzzy, dist */

  tcp_find_match(to_srv, tsig, 1, 0);

  if (tsig->matched)
    FATAL("Signature in line %u is already covered by line %u.",
          line_no, tsig->matched->line_no);

  /* Everything checks out, so let's register it. */

  bucket = opt_hash % SIG_BUCKETS;

  sigs[to_srv][bucket] = DFL_ck_realloc(sigs[to_srv][bucket],
    (sig_cnt[to_srv][bucket] + 1) * sizeof(struct tcp_sig_record));

  trec = sigs[to_srv][bucket] + sig_cnt[to_srv][bucket];

  sig_cnt[to_srv][bucket]++;

  trec->generic  = generic;
  trec->class_id = sig_class;
  trec->name_id  = sig_name;
  trec->flavor   = sig_flavor;
  trec->label_id = label_id;
  trec->sys      = sys;
  trec->sys_cnt  = sys_cnt;
  trec->line_no  = line_no;
  trec->sig      = tsig;
  trec->bad_ttl  = bad_ttl;

  /* All done, phew. */

}


/* Convert struct packet_data to a simplified struct tcp_sig representation
   suitable for signature matching. Compute hashes. */

static void packet_to_sig(struct packet_data* pk, struct tcp_sig* ts) {

  ts->opt_hash = hash32(pk->opt_layout, pk->opt_cnt, hash_seed);

  ts->quirks      = pk->quirks;
  ts->opt_eol_pad = pk->opt_eol_pad;
  ts->ip_opt_len  = pk->ip_opt_len;
  ts->ip_ver      = pk->ip_ver;
  ts->ttl         = pk->ttl;
  ts->mss         = pk->mss;
  ts->win         = pk->win;
  ts->win_type    = WIN_TYPE_NORMAL; /* Keep as-is. */
  ts->wscale      = pk->wscale;
  ts->pay_class   = !!pk->pay_len;
  ts->tot_hdr     = pk->tot_hdr;
  ts->ts1         = pk->ts1;
  ts->recv_ms     = get_unix_time_ms();
  ts->matched     = NULL;
  ts->fuzzy       = 0;
  ts->dist        = 0;

};


/* Dump unknown signature. */

static u8* dump_sig(struct packet_data* pk, struct tcp_sig* ts, u16 syn_mss) {

  static u8* ret;
  u32 rlen = 0;

  u8  win_mtu;
  s16 win_m;
  u32 i;
  u8  dist = guess_dist(pk->ttl);

#define RETF(_par...) do { \
    s32 _len = snprintf(NULL, 0, _par); \
    if (_len < 0) FATAL("Whoa, snprintf() fails?!"); \
    ret = DFL_ck_realloc_kb(ret, rlen + _len + 1); \
    snprintf((char*)ret + rlen, _len + 1, _par); \
    rlen += _len; \
  } while (0)

  if (dist > MAX_DIST) {

    RETF("%u:%u+?:%u:", pk->ip_ver, pk->ttl, pk->ip_opt_len);

  } else {

    RETF("%u:%u+%u:%u:", pk->ip_ver, pk->ttl, dist, pk->ip_opt_len);

  }

  /* Detect a system echoing back MSS from p0f-sendsyn queries, suggest using
     a wildcard in such a case. */

  if (pk->mss == SPECIAL_MSS && pk->tcp_type == (TCP_SYN|TCP_ACK)) RETF("*:");
  else RETF("%u:", pk->mss);

  win_m = detect_win_multi(ts, &win_mtu, syn_mss);

  if (win_m > 0) RETF("%s*%u", win_mtu ? "mtu" : "mss", win_m);
  else RETF("%u", pk->win);

  RETF(",%u:", pk->wscale);

  for (i = 0; i < pk->opt_cnt; i++) {

    switch (pk->opt_layout[i]) {

      case TCPOPT_EOL:
        RETF("%seol+%u", i ? "," : "", pk->opt_eol_pad); break;

      case TCPOPT_NOP:
        RETF("%snop", i ? "," : ""); break;

      case TCPOPT_MAXSEG:
        RETF("%smss", i ? "," : ""); break;

      case TCPOPT_WSCALE:
        RETF("%sws", i ? "," : ""); break;

      case TCPOPT_SACKOK:
        RETF("%ssok", i ? "," : ""); break;

      case TCPOPT_SACK:
        RETF("%ssack", i ? "," : ""); break;

      case TCPOPT_TSTAMP:
        RETF("%sts", i ? "," : ""); break;

      default:
        RETF("%s?%u", i ? "," : "", pk->opt_layout[i]);

    }

  }

  RETF(":");

  if (pk->quirks) {

    u8 sp = 0;

#define MAYBE_CM(_str) do { \
    if (sp) RETF("," _str); else RETF(_str); \
    sp = 1; \
  } while (0)

    if (pk->quirks & QUIRK_DF)      MAYBE_CM("df");
    if (pk->quirks & QUIRK_NZ_ID)   MAYBE_CM("id+");
    if (pk->quirks & QUIRK_ZERO_ID) MAYBE_CM("id-");
    if (pk->quirks & QUIRK_ECN)     MAYBE_CM("ecn");
    if (pk->quirks & QUIRK_NZ_MBZ)  MAYBE_CM("0+");
    if (pk->quirks & QUIRK_FLOW)    MAYBE_CM("flow");

    if (pk->quirks & QUIRK_ZERO_SEQ) MAYBE_CM("seq-");
    if (pk->quirks & QUIRK_NZ_ACK)   MAYBE_CM("ack+");
    if (pk->quirks & QUIRK_ZERO_ACK) MAYBE_CM("ack-");
    if (pk->quirks & QUIRK_NZ_URG)   MAYBE_CM("uptr+");
    if (pk->quirks & QUIRK_URG)      MAYBE_CM("urgf+");
    if (pk->quirks & QUIRK_PUSH)     MAYBE_CM("pushf+");

    if (pk->quirks & QUIRK_OPT_ZERO_TS1) MAYBE_CM("ts1-");
    if (pk->quirks & QUIRK_OPT_NZ_TS2)   MAYBE_CM("ts2+");
    if (pk->quirks & QUIRK_OPT_EOL_NZ)   MAYBE_CM("opt+");
    if (pk->quirks & QUIRK_OPT_EXWS)     MAYBE_CM("exws");
    if (pk->quirks & QUIRK_OPT_BAD)      MAYBE_CM("bad");

#undef MAYBE_CM

  }

  if (pk->pay_len) RETF(":+"); else RETF(":0");

  return ret;

}


/* Dump signature-related flags. */

static u8* dump_flags(struct packet_data* pk, struct tcp_sig* ts) {

  static u8* ret;
  u32 rlen = 0;

  RETF("");

  if (ts->matched) {

    if (ts->matched->generic) RETF(" generic");
    if (ts->fuzzy) RETF(" fuzzy");
    if (ts->matched->bad_ttl) RETF(" random_ttl");

  }

  if (ts->dist > MAX_DIST) RETF(" excess_dist");
  if (pk->tos) RETF(" tos:0x%02x", pk->tos);

  if (*ret) return ret + 1; else return (u8*)"none";

#undef RETF

}


/* Compare current signature with historical data, draw conclusions. This
   is called only for OS sigs. */

static void score_nat(u8 to_srv, struct tcp_sig* sig, struct packet_flow* f) {

  struct host_data* hd;
  struct tcp_sig* ref;
  u8  score = 0, diff_already = 0;
  u16 reason = 0;
  s32 ttl_diff;

  if (to_srv) { 

    hd = f->client;
    ref = hd->last_syn;

  } else {

    hd = f->server;
    ref = hd->last_synack;

  }


  if (!ref) {

    /* No previous signature of matching type at all. We can perhaps still check
       if class / name is the same as on file, as that data might have been
       obtained from other types of sigs. */

    if (sig->matched && hd->last_class_id != -1) {

      if (hd->last_name_id != sig->matched->name_id) {

        DEBUG("[#] New TCP signature different OS type than host data.\n");

        reason |= NAT_OS_SIG;
        score  += 8;

      }

    }

    goto log_and_update;

  }

  /* We have some previous data. */

  if (!sig->matched || !ref->matched) {

    /* One or both of the signatures are unknown. Let's see if they differ.
       The scoring here isn't too strong, because we don't know if the 
       unrecognized signature isn't originating from userland tools. */

    if ((sig->quirks ^ ref->quirks) & ~(QUIRK_ECN|QUIRK_DF|QUIRK_NZ_ID|
        QUIRK_ZERO_ID)) {

      DEBUG("[#] Non-fuzzy quirks changed compared to previous sig.\n");

      reason |= NAT_UNK_DIFF;
      score  += 2;

    } else if (to_srv && sig->opt_hash != ref->opt_hash) {

      /* We only match option layout for SYNs; it may change on SYN+ACK,
         and the user may have gaps in SYN+ACK sigs if he ignored our
         advice on using p0f-sendsyn. */

      DEBUG("[#] SYN option layout changed compared to previous sig.\n");

      reason |= NAT_UNK_DIFF;
      score  += 1;

    }

    /* Progression from known to unknown is also of interest for SYNs. */

    if (to_srv && sig->matched != ref->matched) {

      DEBUG("[#] SYN signature changed from %s.\n",
            sig->matched ? "unknown to known" : "known to unknown");

      score += 1;
      reason |= NAT_TO_UNK;

    }

  } else {

    /* Both signatures known! */

    if (ref->matched->name_id != sig->matched->name_id) {

      DEBUG("[#] TCP signature different OS type on previous sig.\n");
      score += 8;
      reason |= NAT_OS_SIG;

      diff_already = 1;

    } else if (to_srv) {

      /* SYN signatures match superficially, but... */

      if (ref->matched->label_id != sig->matched->label_id) {

        /* SYN label changes are a weak but useful signal. SYN+ACK signatures
           may need less intuitive groupings, so we don't check that. */

        DEBUG("[#] SYN signature label different on previous sig.\n");
        score += 2;
        reason |= NAT_OS_SIG;

      } else if (ref->matched->line_no != sig->matched->line_no) {

        /* Change in line number is an extremely weak but still noteworthy
           signal. */

        DEBUG("[#] SYN signature changes within the same label.\n");
        score += 1;
        reason |= NAT_OS_SIG;

      } else if (sig->fuzzy != ref->fuzzy) {

        /* Fuzziness change on a perfectly matched signature? */

        DEBUG("[#] SYN signature fuzziness changes.\n");
        score += 1;
        reason |= NAT_FUZZY;

      }

    }

  }

  /* Unless the signatures are already known to differ radically, mismatch
     between host data and current sig is of additional note. */

  if (!diff_already && sig->matched && hd->last_class_id != -1 &&
      hd->last_name_id != sig->matched->name_id) {

    DEBUG("[#] New OS signature different OS type than host data.\n");
    score += 8;
    reason |= NAT_OS_SIG;
    diff_already = 1;

  }

  /* TTL differences in absence of major signature mismatches is also
     interesting, unless the signatures are tagged as "bad TTL", or unless
     the difference is barely 1 and the host is distant. */

#define ABS(_x) ((_x) < 0 ? -(_x) : (_x))

  ttl_diff = ((s16)sig->ttl) - ref->ttl;

  if (!diff_already && ttl_diff && (!sig->matched || !sig->matched->bad_ttl) &&
      (!ref->matched || !ref->matched->bad_ttl) && (sig->dist <= NEAR_TTL_LIMIT ||
       ttl_diff > 1)) {

    DEBUG("[#] Signature TTL differs by %d (dist = %u).\n", ttl_diff, sig->dist);

    if (sig->dist > LOCAL_TTL_LIMIT && ABS(ttl_diff) <= SMALL_TTL_CHG)
      score += 1; else score += 4;

    reason |= NAT_TTL;

  }

  /* Source port going back frequently is of some note, although it will happen
     spontaneously every now and then. Require the drop to be by at least
     few dozen, to account for simple case of several simultaneously opened
     connections arriving in odd order. */

  if (to_srv && hd->last_port && f->cli_port < hd->last_port &&
      hd->last_port - f->cli_port >= MIN_PORT_DROP) {

    DEBUG("[#] Source port drops from %u to %u.\n", hd->last_port, f->cli_port);

    score += 1;
    reason |= NAT_PORT;

  }

  /* Change of MTU is always sketchy. */

  if (sig->mss != ref->mss) {

    DEBUG("[#] MSS for signature changed from %u to %u.\n", ref->mss, sig->mss);

    score += 1;
    reason |= NAT_MSS;

  }

  /* Check timestamp progression to possibly adjust current score. Don't rate
     on TS alone, because some systems may be just randomizing that. */

  if (score && sig->ts1 && ref->ts1) {
 
    u64 ms_diff = sig->recv_ms - ref->recv_ms;

    /* Require a timestamp within the last day; if the apparent TS progression
       is much higher than 1 kHz, complain. */

    if (ms_diff < MAX_NAT_TS) {

      u64 use_ms  = (ms_diff < TSTAMP_GRACE) ? TSTAMP_GRACE : ms_diff;
      u64 max_ts  = use_ms * MAX_TSCALE / 1000;

      u32 ts_diff  = sig->ts1 - ref->ts1;

      if (ts_diff > max_ts && (ms_diff >= TSTAMP_GRACE || ~ts_diff > max_ts)) {

        DEBUG("[#] Dodgy timestamp progression across signatures (%d "
              "in %llu ms).\n", ts_diff, ms_diff);
        score += 4;
        reason |= NAT_TS;

      } else {

        DEBUG("[#] Timestamp consistent across signatures (%d in %llu ms), " 
              "reducing score.\n", ts_diff, ms_diff);
        score /= 2;

      }

    } else DEBUG("[#] Timestamps available, but with bad interval (%llu ms).\n",
                 ms_diff);

  }

log_and_update:

  add_nat_score(to_srv, f, reason, score);

  /* Update some of the essential records. */

  if (sig->matched) {

    hd->last_class_id = sig->matched->class_id;
    hd->last_name_id  = sig->matched->name_id;
    hd->last_flavor   = sig->matched->flavor;

    hd->last_quality  = (sig->fuzzy * P0F_MATCH_FUZZY) |
      (sig->matched->generic * P0F_MATCH_GENERIC);

  }

  hd->last_port = f->cli_port;

}


/* Fingerprint SYN or SYN+ACK. */

struct tcp_sig* fingerprint_tcp(u8 to_srv, struct packet_data* pk,
                                struct packet_flow* f) {

  struct tcp_sig* sig;
  struct tcp_sig_record* m;

  sig = ck_alloc(sizeof(struct tcp_sig));
  packet_to_sig(pk, sig);

  /* Detect packets generated by p0f-sendsyn; they require special
     handling to provide the user with response fingerprints, but not
     interfere with NAT scores and such. */

  if (pk->tcp_type == TCP_SYN && pk->win == SPECIAL_WIN &&
      pk->mss == SPECIAL_MSS) f->sendsyn = 1;

  if (to_srv) 
    start_observation(f->sendsyn ? "sendsyn probe" : "syn", 4, 1, f);
  else
    start_observation(f->sendsyn ? "sendsyn response" : "syn+ack", 4, 0, f);

  tcp_find_match(to_srv, sig, 0, f->syn_mss);

  if ((m = sig->matched)) {

    OBSERVF((m->class_id == -1 || f->sendsyn) ? "app" : "os", "%s%s%s",
            fp_os_names[m->name_id], m->flavor ? " " : "",
            m->flavor ? m->flavor : (u8*)"");

  } else {

    add_observation_field("os", NULL);

  }

  if (m && m->bad_ttl) {

    OBSERVF("dist", "<= %u", sig->dist);

  } else {

    if (to_srv) f->client->distance = sig->dist;
    else f->server->distance = sig->dist;
    
    OBSERVF("dist", "%u", sig->dist);

  }

  add_observation_field("params", dump_flags(pk, sig));

  add_observation_field("raw_sig", dump_sig(pk, sig, f->syn_mss));

  if (pk->tcp_type == TCP_SYN) f->syn_mss = pk->mss;

  /* That's about as far as we go with non-OS signatures. */

  if (m && m->class_id == -1) {
    verify_tool_class(to_srv, f, m->sys, m->sys_cnt);
    ck_free(sig);
    return NULL;
  }

  if (f->sendsyn) {
    ck_free(sig);
    return NULL;
  }

  score_nat(to_srv, sig, f);

  return sig;

}


/* Perform uptime detection. This is the only FP function that gets called not
   only on SYN or SYN+ACK, but also on ACK traffic. */

void check_ts_tcp(u8 to_srv, struct packet_data* pk, struct packet_flow* f) {

  u32    ts_diff;
  u64    ms_diff;

  u32    freq;
  u32    up_min, up_mod_days;

  double ffreq;

  if (!pk->ts1 || f->sendsyn) return;

  /* If we're getting SYNs very rapidly, last_syn may be changing too quickly
     to be of any use. Perhaps lock into an older value? */

  if (to_srv) {

     if (f->cli_tps || !f->client->last_syn || !f->client->last_syn->ts1)
       return;

     ms_diff = get_unix_time_ms() - f->client->last_syn->recv_ms;
     ts_diff = pk->ts1 - f->client->last_syn->ts1;

  } else {

     if (f->srv_tps || !f->server->last_synack || !f->server->last_synack->ts1)
        return;

     ms_diff = get_unix_time_ms() - f->server->last_synack->recv_ms;
     ts_diff = pk->ts1 - f->server->last_synack->ts1;
  
  }

  /* Wait at least 25 ms, and not more than 10 minutes, for at least 5
     timestamp ticks. Allow the timestamp to go back slightly within
     a short window, too - we may be receiving packets a bit out of
     order. */

  if (ms_diff < MIN_TWAIT || ms_diff > MAX_TWAIT) return;

  if (ts_diff < 5 || (ms_diff < TSTAMP_GRACE && (~ts_diff) / 1000 < 
      MAX_TSCALE / TSTAMP_GRACE)) return;

  if (ts_diff > ~ts_diff) ffreq = ~ts_diff * -1000.0 / ms_diff;
  else ffreq = ts_diff * 1000.0 / ms_diff;

  if (ffreq < MIN_TSCALE || ffreq > MAX_TSCALE) {

    /* Allow bad reading on SYN, as this may be just an artifact of IP
       sharing or OS change. */

    if (pk->tcp_type != TCP_SYN) {

      if (to_srv) f->cli_tps = -1; else f->srv_tps = -1;

    }

    DEBUG("[#] Bad %s TS frequency: %.02f Hz (%d ticks in %llu ms).\n",
          to_srv ? "client" : "server", ffreq, ts_diff, ms_diff);

    return;

  }

  freq = ffreq;

  /* Round the frequency neatly. */

  switch (freq) {

    case 0:           freq = 1; break;
    case 1 ... 10:    break;
    case 11 ... 50:   freq = (freq + 3) / 5 * 5; break;
    case 51 ... 100:  freq = (freq + 7) / 10 * 10; break;
    case 101 ... 500: freq = (freq + 33) / 50 * 50; break;
    default:          freq = (freq + 67) / 100 * 100; break;

  }

  if (to_srv) f->cli_tps = freq; else f->srv_tps = freq;

  up_min = pk->ts1 / freq / 60;
  up_mod_days = 0xFFFFFFFF / (freq * 60 * 60 * 24);

  start_observation("uptime", 2, to_srv, f);

  if (to_srv) {

    f->client->last_up_min = up_min;
    f->client->up_mod_days = up_mod_days;

  } else {

    f->server->last_up_min = up_min;
    f->server->up_mod_days = up_mod_days;

  }

  OBSERVF("uptime", "%u days %u hrs %u min (modulo %u days)",
          (up_min / 60 / 24), (up_min / 60) % 24, up_min % 60,
          up_mod_days);

  OBSERVF("raw_freq", "%.02f Hz", ffreq);

}
