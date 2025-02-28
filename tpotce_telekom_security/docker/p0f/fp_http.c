/*
   p0f - HTTP fingerprinting
   -------------------------

   Copyright (C) 2012 by Michal Zalewski <lcamtuf@coredump.cx>

   Distributed under the terms and conditions of GNU LGPL.

 */

#define _FROM_FP_HTTP
#define _GNU_SOURCE

#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <ctype.h>
#include <time.h>

#include <netinet/in.h>
#include <sys/types.h>

#include "types.h"
#include "config.h"
#include "debug.h"
#include "alloc-inl.h"
#include "process.h"
#include "readfp.h"
#include "p0f.h"
#include "tcp.h"
#include "hash.h"

#include "fp_http.h"
#include "languages.h"

static u8** hdr_names;                 /* List of header names by ID         */
static u32  hdr_cnt;                   /* Number of headers registered       */

static u32* hdr_by_hash[SIG_BUCKETS];  /* Hashed header names                */
static u32  hbh_cnt[SIG_BUCKETS];      /* Number of headers in bucket        */

/* Signatures aren't bucketed due to the complex matching used; but we use
   Bloom filters to go through them quickly. */

static struct http_sig_record* sigs[2];
static u32 sig_cnt[2];

static struct ua_map_record* ua_map;   /* Mappings between U-A and OS        */
static u32 ua_map_cnt;

#define SLOF(_str) (u8*)_str, strlen((char*)_str)


/* Ghetto Bloom filter 4-out-of-64 bitmask generator for adding 32-bit header
   IDs to a set. We expect around 10 members in a set. */

static inline u64 bloom4_64(u32 val) {
  u32 hash = hash32(&val, 4, hash_seed);
  u64 ret;
  ret = (1LL << (hash & 63));
  ret ^= (1LL << ((hash >> 8) & 63));
  ret ^= (1LL << ((hash >> 16) & 63));
  ret ^= (1LL << ((hash >> 24) & 63));
  return ret;
}


/* Look up or register new header */

static s32 lookup_hdr(u8* name, u32 len, u8 create) {

  u32  bucket = hash32(name, len, hash_seed) % SIG_BUCKETS;

  u32* p = hdr_by_hash[bucket];
  u32  i = hbh_cnt[bucket];

  while (i--) {
    if (!memcmp(hdr_names[*p], name, len) /* ASAN won't like this... */ &&
        !hdr_names[*p][len]) return *p;
    p++;
  }

  /* Not found! */

  if (!create) return -1;

  hdr_names = DFL_ck_realloc(hdr_names, (hdr_cnt + 1) * sizeof(u8*));
  hdr_names[hdr_cnt] = DFL_ck_memdup_str(name, len);

  hdr_by_hash[bucket] = DFL_ck_realloc(hdr_by_hash[bucket],
    (hbh_cnt[bucket] + 1) * 4);

  hdr_by_hash[bucket][hbh_cnt[bucket]++] = hdr_cnt++;

  return hdr_cnt - 1;

}


/* Pre-register essential headers. */

void http_init(void) {
  u32 i;

  /* Do not change - other code depends on the ordering of first 6 entries. */

  lookup_hdr(SLOF("User-Agent"), 1);      /* 0 */
  lookup_hdr(SLOF("Server"), 1);          /* 1 */
  lookup_hdr(SLOF("Accept-Language"), 1); /* 2 */
  lookup_hdr(SLOF("Via"), 1);             /* 3 */
  lookup_hdr(SLOF("X-Forwarded-For"), 1); /* 4 */
  lookup_hdr(SLOF("Date"), 1);            /* 5 */

#define HDR_UA  0
#define HDR_SRV 1
#define HDR_AL  2
#define HDR_VIA 3
#define HDR_XFF 4
#define HDR_DAT 5

  i = 0;
  while (req_optional[i].name) {
    req_optional[i].id = lookup_hdr(SLOF(req_optional[i].name), 1);
    i++;
  }

  i = 0;
  while (resp_optional[i].name) {
    resp_optional[i].id = lookup_hdr(SLOF(resp_optional[i].name), 1);
    i++;
  }

  i = 0;
  while (req_skipval[i].name) {
    req_skipval[i].id = lookup_hdr(SLOF(req_skipval[i].name), 1);
    i++;
  }

  i = 0;
  while (resp_skipval[i].name) {
    resp_skipval[i].id = lookup_hdr(SLOF(resp_skipval[i].name), 1);
    i++;
  }

  i = 0;
  while (req_common[i].name) {
    req_common[i].id = lookup_hdr(SLOF(req_common[i].name), 1);
    i++;
  }

  i = 0;
  while (resp_common[i].name) {
    resp_common[i].id = lookup_hdr(SLOF(resp_common[i].name), 1);
    i++;
  }

}


/* Find match for a signature. */

static void http_find_match(u8 to_srv, struct http_sig* ts, u8 dupe_det) {

  struct http_sig_record* gmatch = NULL;
  struct http_sig_record* ref = sigs[to_srv];
  u32 cnt = sig_cnt[to_srv];

  while (cnt--) {

    struct http_sig* rs = ref->sig;
    u32 ts_hdr = 0, rs_hdr = 0;

    if (rs->http_ver != -1 && rs->http_ver != ts->http_ver) goto next_sig;

    /* Check that all the headers listed for the p0f.fp signature (probably)
       appear in the examined traffic. */

    if ((ts->hdr_bloom4 & rs->hdr_bloom4) != rs->hdr_bloom4) goto next_sig;

    /* Confirm the ordering and values of headers (this is relatively
       slow, hence the Bloom filter first). */

    while (rs_hdr < rs->hdr_cnt) {

      u32 orig_ts = ts_hdr;

      while (rs->hdr[rs_hdr].id != ts->hdr[ts_hdr].id &&
             ts_hdr < ts->hdr_cnt) ts_hdr++;

      if (ts_hdr == ts->hdr_cnt) {

        if (!rs->hdr[rs_hdr].optional) goto next_sig;

        /* If this is an optional header, check that it doesn't appear
           anywhere else. */

        for (ts_hdr = 0; ts_hdr < ts->hdr_cnt; ts_hdr++)
          if (rs->hdr[rs_hdr].id == ts->hdr[ts_hdr].id) goto next_sig;

        ts_hdr = orig_ts;
        rs_hdr++;
        continue;

      }

      if (rs->hdr[rs_hdr].value &&
          (!ts->hdr[ts_hdr].value ||
          !strstr((char*)ts->hdr[ts_hdr].value,
          (char*)rs->hdr[rs_hdr].value))) goto next_sig;

      ts_hdr++;
      rs_hdr++;

    }

    /* Check that the headers forbidden in p0f.fp don't appear in the traffic.
       We first check if they seem to appear in ts->hdr_bloom4, and only if so,
       we do a full check. */

    for (rs_hdr = 0; rs_hdr < rs->miss_cnt; rs_hdr++) {

      u64 miss_bloom4 = bloom4_64(rs->miss[rs_hdr]);

      if ((ts->hdr_bloom4 & miss_bloom4) != miss_bloom4) continue;

      /* Okay, possible instance of a banned header - scan list... */

      for (ts_hdr = 0; ts_hdr < ts->hdr_cnt; ts_hdr++)
        if (rs->miss[rs_hdr] == ts->hdr[ts_hdr].id) goto next_sig;

    }

    /* When doing dupe detection, we want to allow a signature with additional
       banned headers to precede one with fewer, or with a different set. */

    if (dupe_det) {

      if (rs->miss_cnt > ts->miss_cnt) goto next_sig;

      for (rs_hdr = 0; rs_hdr < rs->miss_cnt; rs_hdr++) {

        for (ts_hdr = 0; ts_hdr < ts->miss_cnt; ts_hdr++) 
          if (rs->miss[rs_hdr] == ts->miss[ts_hdr]) break;

        /* One of the reference headers doesn't appear in current sig! */

        if (ts_hdr == ts->miss_cnt) goto next_sig;

      }


    }

    /* Whoa, a match. */

    if (!ref->generic) {

      ts->matched = ref;

      if (rs->sw && ts->sw && !strstr((char*)ts->sw, (char*)rs->sw))
        ts->dishonest = 1;

      return;

    } else if (!gmatch) gmatch = ref;

next_sig:

    ref = ref + 1;

  }

  /* A generic signature is the best we could find. */

  if (!dupe_det && gmatch) {

    ts->matched = gmatch;

    if (gmatch->sig->sw && ts->sw && !strstr((char*)ts->sw,
        (char*)gmatch->sig->sw)) ts->dishonest = 1;

  }

}


/* Register new HTTP signature. */

void http_register_sig(u8 to_srv, u8 generic, s32 sig_class, u32 sig_name,
                       u8* sig_flavor, u32 label_id, u32* sys, u32 sys_cnt,
                       u8* val, u32 line_no) {

  struct http_sig* hsig;
  struct http_sig_record* hrec;

  u8* nxt;

  hsig = DFL_ck_alloc(sizeof(struct http_sig));

  sigs[to_srv] = DFL_ck_realloc(sigs[to_srv], sizeof(struct http_sig_record) *
    (sig_cnt[to_srv] + 1));

  hrec = &sigs[to_srv][sig_cnt[to_srv]];

  if (val[1] != ':') FATAL("Malformed signature in line %u.", line_no);

  /* http_ver */

  switch (*val) {
    case '0': break;
    case '1': hsig->http_ver = 1; break;
    case '*': hsig->http_ver = -1; break;
    default: FATAL("Bad HTTP version in line %u.", line_no);
  }

  val += 2;

  /* horder */

  while (*val != ':') {

    u32 id;
    u8 optional = 0;

    if (hsig->hdr_cnt >= HTTP_MAX_HDRS)
      FATAL("Too many headers listed in line %u.", line_no);

    nxt = val;

    if (*nxt == '?') { optional = 1; val++; nxt++; }

    while (isalnum(*nxt) || *nxt == '-' || *nxt == '_') nxt++;

    if (val == nxt)
      FATAL("Malformed header name in line %u.", line_no);

    id = lookup_hdr(val, nxt - val, 1);

    hsig->hdr[hsig->hdr_cnt].id = id;
    hsig->hdr[hsig->hdr_cnt].optional = optional;

    if (!optional) hsig->hdr_bloom4 |= bloom4_64(id);

    val = nxt;

    if (*val == '=') {

      if (val[1] != '[') 
        FATAL("Missing '[' after '=' in line %u.", line_no);

      val += 2;
      nxt = val;

      while (*nxt && *nxt != ']') nxt++;

      if (val == nxt || !*nxt)
        FATAL("Malformed signature in line %u.", line_no);

      hsig->hdr[hsig->hdr_cnt].value = DFL_ck_memdup_str(val, nxt - val);

      val = nxt + 1;

    }

    hsig->hdr_cnt++;

    if (*val == ',') val++; else if (*val != ':')
      FATAL("Malformed signature in line %u.", line_no);

  }

  val++;

  /* habsent */

  while (*val != ':') {

    u32 id;

    if (hsig->miss_cnt >= HTTP_MAX_HDRS)
      FATAL("Too many headers listed in line %u.", line_no);

    nxt = val;
    while (isalnum(*nxt) || *nxt == '-' || *nxt == '_') nxt++;

    if (val == nxt)
      FATAL("Malformed header name in line %u.", line_no);

    id = lookup_hdr(val, nxt - val, 1);

    hsig->miss[hsig->miss_cnt] = id;

    val = nxt;

    hsig->miss_cnt++;

    if (*val == ',') val++; else if (*val != ':')
      FATAL("Malformed signature in line %u.", line_no);

  }

  val++;

  /* exp_sw */

  if (*val) {

    if (strchr((char*)val, ':'))
      FATAL("Malformed signature in line %u.", line_no);

    hsig->sw = DFL_ck_strdup(val);

  }

  http_find_match(to_srv, hsig, 1);

  if (hsig->matched)
    FATAL("Signature in line %u is already covered by line %u.",
          line_no, hsig->matched->line_no);

  hrec->class_id = sig_class;
  hrec->name_id  = sig_name;
  hrec->flavor   = sig_flavor;
  hrec->label_id = label_id;
  hrec->sys      = sys;
  hrec->sys_cnt  = sys_cnt;
  hrec->line_no  = line_no;
  hrec->generic  = generic;

  hrec->sig      = hsig;

  sig_cnt[to_srv]++;

}


/* Register new HTTP signature. */

void http_parse_ua(u8* val, u32 line_no) {

  u8* nxt;

  while (*val) {

    u32 id;
    u8* name = NULL;

    nxt = val;
    while (*nxt && (isalnum(*nxt) || strchr(NAME_CHARS, *nxt))) nxt++;

    if (val == nxt)
      FATAL("Malformed system name in line %u.", line_no);

    id = lookup_name_id(val, nxt - val);

    val = nxt;

    if (*val == '=') {

      if (val[1] != '[') 
        FATAL("Missing '[' after '=' in line %u.", line_no);

      val += 2;
      nxt = val;

      while (*nxt && *nxt != ']') nxt++;

      if (val == nxt || !*nxt)
        FATAL("Malformed signature in line %u.", line_no);

      name = DFL_ck_memdup_str(val, nxt - val);

      val = nxt + 1;

    }

    ua_map = DFL_ck_realloc(ua_map, (ua_map_cnt + 1) *
                        sizeof(struct ua_map_record));

    ua_map[ua_map_cnt].id = id;
   
    if (!name) ua_map[ua_map_cnt].name = fp_os_names[id];
      else ua_map[ua_map_cnt].name = name;

    ua_map_cnt++;

    if (*val == ',') val++;

  }

}


/* Dump a HTTP signature. */

static u8* dump_sig(u8 to_srv, struct http_sig* hsig) {

  u32 i;
  u8 had_prev = 0;
  struct http_id* list;

  u8 tmp[HTTP_MAX_SHOW + 1];
  u32 tpos;

  static u8* ret;
  u32 rlen = 0;

  u8* val;

#define RETF(_par...) do { \
    s32 _len = snprintf(NULL, 0, _par); \
    if (_len < 0) FATAL("Whoa, snprintf() fails?!"); \
    ret = DFL_ck_realloc_kb(ret, rlen + _len + 1); \
    snprintf((char*)ret + rlen, _len + 1, _par); \
    rlen += _len; \
  } while (0)
    
  RETF("%u:", hsig->http_ver);

  for (i = 0; i < hsig->hdr_cnt; i++) {

    if (hsig->hdr[i].id >= 0) {

      u8 optional = 0;

      /* Check the "optional" list. */

      list = to_srv ? req_optional : resp_optional;

      while (list->name) {
        if (list->id == hsig->hdr[i].id) break;
        list++;
      }

      if (list->name) optional = 1;

      RETF("%s%s%s", had_prev ? "," : "", optional ? "?" : "",
           hdr_names[hsig->hdr[i].id]);
      had_prev = 1;

      if (!(val = hsig->hdr[i].value)) continue;

      /* Next, make sure that the value is not on the ignore list. */

      if (optional) continue;

      list = to_srv ? req_skipval : resp_skipval;

      while (list->name) {
        if (list->id == hsig->hdr[i].id) break;
        list++;
      }

      if (list->name) continue;

      /* Looks like it's not on the list, so let's output a cleaned-up version
         up to HTTP_MAX_SHOW. */

      tpos = 0;

      while (tpos < HTTP_MAX_SHOW && val[tpos] >= 0x20 && val[tpos] < 0x80 &&
             val[tpos] != ']' && val[tpos] != '|') {

        tmp[tpos] = val[tpos];
        tpos++;

      }

      tmp[tpos] = 0;

      if (!tpos) continue;

      RETF("=[%s]", tmp);

    } else {

      RETF("%s%s", had_prev ? "," : "", hsig->hdr[i].name);
      had_prev = 1;

      if (!(val = hsig->hdr[i].value)) continue;

      tpos = 0;

      while (tpos < HTTP_MAX_SHOW && val[tpos] >= 0x20 && val[tpos] < 0x80 &&
             val[tpos] != ']') { tmp[tpos] = val[tpos]; tpos++; }

      tmp[tpos] = 0;

      if (!tpos) continue;

      RETF("=[%s]", tmp);

    }

  }

  RETF(":");

  list = to_srv ? req_common : resp_common;
  had_prev = 0;

  while (list->name) {

    for (i = 0; i < hsig->hdr_cnt; i++) 
      if (hsig->hdr[i].id == list->id) break;

    if (i == hsig->hdr_cnt) {
      RETF("%s%s", had_prev ? "," : "", list->name);
      had_prev = 1;
    }

    list++;

  }

  RETF(":");

  if ((val = hsig->sw)) {

    tpos = 0;

    while (tpos < HTTP_MAX_SHOW &&  val[tpos] >= 0x20 && val[tpos] < 0x80 &&
           val[tpos] != ']') { tmp[tpos] = val[tpos]; tpos++; }

    tmp[tpos] = 0;

    if (tpos) RETF("%s", tmp);

  }

  return ret;


}


/* Dump signature flags. */

static u8* dump_flags(struct http_sig* hsig, struct http_sig_record* m) {

  static u8* ret;
  u32 rlen = 0;

  RETF("");

  if (hsig->dishonest) RETF(" dishonest");
  if (!hsig->sw) RETF(" anonymous");
  if (m && m->generic) RETF(" generic");

#undef RETF

  if (*ret) return ret + 1; else return (u8*)"none";

}


/* Score signature differences. For unknown signatures, the presumption is that
   they identify apps, so the logic is quite different from TCP. */

static void score_nat(u8 to_srv, struct packet_flow* f) {

  struct http_sig_record* m = f->http_tmp.matched;
  struct host_data* hd;
  struct http_sig* ref;

  u8  score = 0, diff_already = 0;
  u16 reason = 0;

  if (to_srv) {

    hd = f->client;
    ref = hd->http_req_os;

  } else {

    hd = f->server;
    ref = hd->http_resp;

    /* If the signature is for a different port, don't read too much into it. */

    if (hd->http_resp_port != f->srv_port) ref = NULL;

  }

  if (!m) {

    /* No match. The user is probably running another app; this is only of
       interest if a server progresses from known to unknown. We can't
       compare two unknown server sigs with that much confidence. */

    if (!to_srv && ref && ref->matched) {

      DEBUG("[#] HTTP server signature changed from known to unknown.\n");
      score  += 4;
      reason |= NAT_TO_UNK;

    }

    goto header_check;

  }

  if (m->class_id == -1) {

    /* Got a match for an application signature. Make sure it runs on the
       OS we have on file... */

    verify_tool_class(to_srv, f, m->sys, m->sys_cnt);

    /* ...and check for inconsistencies in server behavior. */

    if (!to_srv && ref && ref->matched) {

      if (ref->matched->name_id != m->name_id) {

        DEBUG("[#] Name on the matched HTTP server signature changes.\n");
        score  += 8;
        reason |= NAT_APP_LB;

      } else if (ref->matched->label_id != m->label_id) {

        DEBUG("[#] Label on the matched HTTP server signature changes.\n");
        score  += 2;
        reason |= NAT_APP_LB;

      }

    }

  } else {

    /* Ooh, spiffy: a match for an OS signature! There will be about two uses
       for this code, ever. */

    if (ref && ref->matched) {

      if (ref->matched->name_id != m->name_id) {

        DEBUG("[#] Name on the matched HTTP OS signature changes.\n");
        score  += 8;
        reason |= NAT_OS_SIG;
        diff_already = 1;

      } else if (ref->matched->name_id != m->label_id) {

        DEBUG("[#] Label on the matched HTTP OS signature changes.\n");
        score  += 2;
        reason |= NAT_OS_SIG;

      }

    } else if (ref) {

      DEBUG("[#] HTTP OS signature changed from unknown to known.\n");
      score  += 4;
      reason |= NAT_TO_UNK;

    }

    /* If we haven't pointed out anything major yet, also complain if the
       signature doesn't match host data. */

    if (!diff_already && hd->last_name_id != m->name_id) {

      DEBUG("[#] Matched HTTP OS signature different than host data.\n");
      score  += 4;
      reason |= NAT_OS_SIG;

    }

  }

  /* If we have determined that U-A looks legit, but the OS doesn't match,
     that's a clear sign of trouble. */

  if (to_srv && m->class_id == -1 && f->http_tmp.sw && !f->http_tmp.dishonest) {

    u32 i;

    for (i = 0; i < ua_map_cnt; i++)
      if (strstr((char*)f->http_tmp.sw, (char*)ua_map[i].name)) break;

    if (i != ua_map_cnt) {

      if (ua_map[i].id != hd->last_name_id) {

        DEBUG("[#] Otherwise plausible User-Agent points to another OS.\n");
        score  += 4;
        reason |= NAT_APP_UA;

        if (!hd->bad_sw) hd->bad_sw = 1;

      } else {

        DEBUG("[#] User-Agent OS value checks out.\n");
        hd->bad_sw = 0;

      }

    }

  }


header_check:

  /* Okay, some last-resort checks. This is obviously concerning: */

  if (f->http_tmp.via) {
    DEBUG("[#] Explicit use of Via or X-Forwarded-For.\n");
    score  += 8;
    reason |= NAT_APP_VIA;
  }

  /* Last but not least, see what happened to 'Date': */

  if (ref && !to_srv && ref->date && f->http_tmp.date) {

    s64 recv_diff = ((s64)f->http_tmp.recv_date) - ref->recv_date;
    s64 hdr_diff  = ((s64)f->http_tmp.date) - ref->date;

    if (hdr_diff < -HTTP_MAX_DATE_DIFF || 
        hdr_diff > recv_diff + HTTP_MAX_DATE_DIFF) {

      DEBUG("[#] HTTP 'Date' distance too high (%lld in %lld sec).\n",
             hdr_diff, recv_diff);
      score  += 4;
      reason |= NAT_APP_DATE;

    } else {

      DEBUG("[#] HTTP 'Date' distance seems fine (%lld in %lld sec).\n",
             hdr_diff, recv_diff);

    }

  }

  add_nat_score(to_srv, f, reason, score);

}


/* Look up HTTP signature, create an observation. */

static void fingerprint_http(u8 to_srv, struct packet_flow* f) {

  struct http_sig_record* m;
  u8* lang = NULL;

  http_find_match(to_srv, &f->http_tmp, 0);

  start_observation(to_srv ? "http request" : "http response", 4, to_srv, f);

  if ((m = f->http_tmp.matched)) {

    OBSERVF((m->class_id < 0) ? "app" : "os", "%s%s%s",
            fp_os_names[m->name_id], m->flavor ? " " : "",
            m->flavor ? m->flavor : (u8*)"");

  } else add_observation_field("app", NULL);

  if (f->http_tmp.lang && isalpha(f->http_tmp.lang[0]) &&
      isalpha(f->http_tmp.lang[1]) && !isalpha(f->http_tmp.lang[2])) {

    u8 lh = LANG_HASH(f->http_tmp.lang[0], f->http_tmp.lang[1]);
    u8 pos = 0;

    while (languages[lh][pos]) {
      if (f->http_tmp.lang[0] == languages[lh][pos][0] &&
          f->http_tmp.lang[1] == languages[lh][pos][1]) break;
      pos += 2;
    }

    if (!languages[lh][pos]) add_observation_field("lang", NULL);
      else add_observation_field("lang", 
           (lang = (u8*)languages[lh][pos + 1]));

  } else add_observation_field("lang", (u8*)"none");

  add_observation_field("params", dump_flags(&f->http_tmp, m));

  add_observation_field("raw_sig", dump_sig(to_srv, &f->http_tmp));

  score_nat(to_srv, f);

  /* Save observations needed to score future responses. */

  if (!to_srv) {

    /* For server response, always store the signature. */

    ck_free(f->server->http_resp);
    f->server->http_resp = ck_memdup(&f->http_tmp, sizeof(struct http_sig));

    f->server->http_resp->hdr_cnt = 0;
    f->server->http_resp->sw   = NULL;
    f->server->http_resp->lang = NULL;
    f->server->http_resp->via  = NULL;

    f->server->http_resp_port = f->srv_port;

    if (lang) f->server->language = lang;

    if (m) {

      if (m->class_id != -1) {

        /* If this is an OS signature, update host record. */

        f->server->last_class_id = m->class_id;
        f->server->last_name_id  = m->name_id;
        f->server->last_flavor   = m->flavor;
        f->server->last_quality  = (m->generic * P0F_MATCH_GENERIC);

      } else {

        /* Otherwise, record app data. */

        f->server->http_name_id = m->name_id;
        f->server->http_flavor  = m->flavor;

        if (f->http_tmp.dishonest) f->server->bad_sw = 2;

      }

    }

  } else {

    if (lang) f->client->language = lang;

    if (m) {

      if (m->class_id != -1) {

        /* Client request - only OS sig is of any note. */

        ck_free(f->client->http_req_os);
        f->client->http_req_os = ck_memdup(&f->http_tmp,
          sizeof(struct http_sig));

        f->client->http_req_os->hdr_cnt = 0;
        f->client->http_req_os->sw   = NULL;
        f->client->http_req_os->lang = NULL;
        f->client->http_req_os->via  = NULL;

        f->client->last_class_id = m->class_id;
        f->client->last_name_id  = m->name_id;
        f->client->last_flavor   = m->flavor;

        f->client->last_quality  = (m->generic * P0F_MATCH_GENERIC);

      } else {

        /* Record app data for the API. */

        f->client->http_name_id = m->name_id;
        f->client->http_flavor  = m->flavor;

        if (f->http_tmp.dishonest) f->client->bad_sw = 2;

      }
 
    }

  }

}



/* Free up any allocated strings in http_sig. */

void free_sig_hdrs(struct http_sig* h) {

  u32 i;

  for (i = 0; i < h->hdr_cnt; i++) {
    if (h->hdr[i].name) ck_free(h->hdr[i].name);
    if (h->hdr[i].value) ck_free(h->hdr[i].value);
  }

}


/* Parse HTTP date field. */

static u32 parse_date(u8* str) {
  struct tm t;

  if (!strptime((char*)str, "%a, %d %b %Y %H:%M:%S %Z", &t)) {
    DEBUG("[#] Invalid 'Date' field ('%s').\n", str);
    return 0;
  }

  return mktime(&t);

}


/* Parse name=value pairs into a signature. */

static u8 parse_pairs(u8 to_srv, struct packet_flow* f, u8 can_get_more) {

  u32 plen = to_srv ? f->req_len : f->resp_len;

  u32 off;

  /* Try to parse name: value pairs. */

  while ((off = f->http_pos) < plen) {

    u8* pay = to_srv ? f->request : f->response;

    u32 nlen, vlen, vstart;
    s32 hid;
    u32 hcount;

    /* Empty line? Dispatch for fingerprinting! */

    if (pay[off] == '\r' || pay[off] == '\n') {

      f->http_tmp.recv_date = get_unix_time();

      fingerprint_http(to_srv, f);

      /* If this is a request, flush the collected signature and prepare
         for parsing the response. If it's a response, just shut down HTTP
         parsing on this flow. */

      if (to_srv) {

        f->http_req_done = 1;
        f->http_pos = 0;

        free_sig_hdrs(&f->http_tmp);
        memset(&f->http_tmp, 0, sizeof(struct http_sig));

        return 1;

      } else {

        f->in_http = -1;
        return 0;

      }

    }

    /* Looks like we're getting a header value. See if we have room for it. */

    if ((hcount = f->http_tmp.hdr_cnt) >= HTTP_MAX_HDRS) {

      DEBUG("[#] Too many HTTP headers in a %s.\n", to_srv ? "request" :
            "response");

      f->in_http = -1;
      return 0;

    }

    /* Try to extract header name. */
      
    nlen = 0;

    while ((isalnum(pay[off]) || pay[off] == '-' || pay[off] == '_') &&
           off < plen && nlen <= HTTP_MAX_HDR_NAME) {

      off++;
      nlen++;

    }

    if (off == plen) {

      if (!can_get_more) {

        DEBUG("[#] End of HTTP %s before end of headers.\n",
              to_srv ? "request" : "response");
        f->in_http = -1;

      }

      return can_get_more;

    }

    /* Empty, excessively long, or non-':'-followed header name? */

    if (!nlen || pay[off] != ':' || nlen > HTTP_MAX_HDR_NAME) {

      DEBUG("[#] Invalid HTTP header encountered (len = %u, char = 0x%02x).\n",
            nlen, pay[off]);

      f->in_http = -1;
      return 0;

    }

    /* At this point, header name starts at f->http_pos, and has nlen bytes.
       Skip ':' and a subsequent whitespace next. */

    off++;

    if (off < plen && isblank(pay[off])) off++;

    vstart = off;
    vlen = 0;

    /* Find the next \n. */

    while (off < plen && vlen <= HTTP_MAX_HDR_VAL && pay[off] != '\n') {

      off++;
      vlen++;

    }

    if (vlen > HTTP_MAX_HDR_VAL) {
      DEBUG("[#] HTTP %s header value length exceeded.\n",
            to_srv ? "request" : "response");
      f->in_http = -1;
      return -1;
    }

    if (off == plen) {

      if (!can_get_more) {
        DEBUG("[#] End of HTTP %s before end of headers.\n",
              to_srv ? "request" : "response");
        f->in_http = -1;
      }

      return can_get_more;

    }

    /* If party is using \r\n terminators, go back one char. */

    if (pay[off - 1] == '\r') vlen--;
 
    /* Header value starts at vstart, and has vlen bytes (may be zero). Record
       this in the signature. */

    hid = lookup_hdr(pay + f->http_pos, nlen, 0);

    f->http_tmp.hdr[hcount].id = hid;

    if (hid < 0) {

      /* Header ID not found, store literal value. */

      f->http_tmp.hdr[hcount].name = ck_memdup_str(pay + f->http_pos, nlen);

    } else {

      /* Found - update Bloom filter. */

      f->http_tmp.hdr_bloom4 |= bloom4_64(hid);

    }

    /* If there's a value, store that too. For U-A and Server, also update
       'sw'; and for requests, collect Accept-Language. */

    if (vlen) {

      u8* val = ck_memdup_str(pay + vstart, vlen);

      f->http_tmp.hdr[hcount].value = val;

      if (to_srv) {

        switch (hid) {
          case HDR_UA: f->http_tmp.sw = val; break;
          case HDR_AL: f->http_tmp.lang = val; break;
          case HDR_VIA:
          case HDR_XFF: f->http_tmp.via = val; break;
        }

      } else {

        switch (hid) {

          case HDR_SRV: f->http_tmp.sw = val; break;
          case HDR_DAT: f->http_tmp.date = parse_date(val); break;
          case HDR_VIA:
          case HDR_XFF: f->http_tmp.via = val; break;

        }

      }

    }

    /* Moving on... */

    f->http_tmp.hdr_cnt++;
    f->http_pos = off + 1; 

  }

  if (!can_get_more) {
    DEBUG("[#] End of HTTP %s before end of headers.\n",
          to_srv ? "request" : "response");
    f->in_http = -1;
  }

  return can_get_more;

}


/* Examine request or response; returns 1 if more data needed and plausibly can
   be read. Note that the buffer is always NUL-terminated. */

u8 process_http(u8 to_srv, struct packet_flow* f) {

  /* Already decided this flow is not worth tracking? */

  if (f->in_http < 0) return 0;

  if (to_srv) {

    u8* pay = f->request;
    u8 can_get_more = (f->req_len < MAX_FLOW_DATA);
    u32 off;

    /* Request done, but pending response? */

    if (f->http_req_done) return 1;

    if (!f->in_http) {

      u8 chr;
      u8* sig_at;

      /* Ooh, new flow! */

      if (f->req_len < 15) return can_get_more;

      /* Scan until \n, or until binary data spotted. */

      off = f->http_pos;

      /* We only care about GET and HEAD requests at this point. */

      if (!off && strncmp((char*)pay, "GET /", 5) &&
          strncmp((char*)pay, "HEAD /", 6)) {
        DEBUG("[#] Does not seem like a GET / HEAD request.\n");
        f->in_http = -1;
        return 0;
      }

      while (off < f->req_len && off < HTTP_MAX_URL &&
             (chr = pay[off]) != '\n') {

        if (chr != '\r' && (chr < 0x20 || chr > 0x7f)) {

          DEBUG("[#] Not HTTP - character 0x%02x encountered.\n", chr);

          f->in_http = -1;
          return 0;
        }

        off++;
  
      }

      /* Newline too far or too close? */

      if (off == HTTP_MAX_URL || off < 14) {

        DEBUG("[#] Not HTTP - newline offset %u.\n", off);

        f->in_http = -1;
        return 0;

      }

      /* Not enough data yet? */

      if (off == f->req_len) {

        f->http_pos = off;

        if (!can_get_more) {

          DEBUG("[#] Not HTTP - no opening line found.\n");
          f->in_http = -1;

        }

        return can_get_more;

      }

      sig_at = pay + off - 8;
      if (pay[off - 1] == '\r') sig_at--;

      /* Bad HTTP/1.x signature? */

      if (strncmp((char*)sig_at, "HTTP/1.", 7)) {

        DEBUG("[#] Not HTTP - bad signature.\n");

        f->in_http = -1;
        return 0;

      }

      f->http_tmp.http_ver = (sig_at[7] == '1');

      f->in_http  = 1;
      f->http_pos = off + 1;

      DEBUG("[#] HTTP detected.\n");

    }

    return parse_pairs(1, f, can_get_more);

  } else {

    u8* pay = f->response;
    u8 can_get_more = (f->resp_len < MAX_FLOW_DATA);
    u32 off;

    /* Response before request? Bail out. */

    if (!f->in_http || !f->http_req_done) {
      f->in_http = -1;
      return 0;
    }

    if (!f->http_gotresp1) {

      u8 chr;

      if (f->resp_len < 13) return can_get_more;

      /* Scan until \n, or until binary data spotted. */

      off = f->http_pos;

      while (off < f->resp_len && off < HTTP_MAX_URL &&
             (chr = pay[off]) != '\n') {

        if (chr != '\r' && (chr < 0x20 || chr > 0x7f)) {

          DEBUG("[#] Invalid HTTP response - character 0x%02x encountered.\n",
                chr);
          f->in_http = -1;
          return 0;

        }

        off++;
  
      }

      /* Newline too far or too close? */

      if (off == HTTP_MAX_URL || off < 13) {

        DEBUG("[#] Invalid HTTP response - newline offset %u.\n", off);

        f->in_http = -1;
        return 0;

      }

      /* Not enough data yet? */

      if (off == f->resp_len) {

        f->http_pos = off;

        if (!can_get_more) {

          DEBUG("[#] Invalid HTTP response - no opening line found.\n");
          f->in_http = -1;

        }

        return can_get_more;

      }

      /* Bad HTTP/1.x signature? */

      if (strncmp((char*)pay, "HTTP/1.", 7)) {

        DEBUG("[#] Invalid HTTP response - bad signature.\n");

        f->in_http = -1;
        return 0;

      }

      f->http_tmp.http_ver = (pay[7] == '1');

      f->http_pos = off + 1;

      DEBUG("[#] HTTP response starts correctly.\n");


    }

    return parse_pairs(0, f, can_get_more);

  }

}
