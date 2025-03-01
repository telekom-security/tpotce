/*
   p0f - vaguely configurable bits
   -------------------------------

   Copyright (C) 2012 by Michal Zalewski <lcamtuf@coredump.cx>

   Distributed under the terms and conditions of GNU LGPL.

 */

#ifndef _HAVE_CONFIG_H
#define _HAVE_CONFIG_H

#include "types.h"

/********************************************
 * Things you may reasonably want to change *
 ********************************************/

/* Default location of p0f.fp: */

#ifndef FP_FILE
#  define FP_FILE           "p0f.fp"
#endif /* !FP_FILE */

/* Initial permissions on log files: */

#ifndef LOG_MODE
#  define LOG_MODE          0600
#endif /* !LOG_MODE */

/* Initial permissions on API sockets: */

#ifndef API_MODE
#  define API_MODE          0666
#endif /* !API_MODE */

/* Default connection and host cache sizes (adjustable via -m): */

#ifndef MAX_HOSTS
#  define MAX_CONN          1000
#  define MAX_HOSTS         10000
#endif /* !MAX_HOSTS */

/* Default connection and host time limits (adjustable via -t): */

#ifndef HOST_IDLE_LIMIT
#  define CONN_MAX_AGE      30  /* seconds */
#  define HOST_IDLE_LIMIT   120 /* minutes */
#endif /* !HOST_IDLE_LIMIT */

/* Default number of API connections permitted (adjustable via -c): */

#ifndef API_MAX_CONN
#  define API_MAX_CONN      20
#endif /* !API_MAX_CONN */

/* Maximum TTL distance for non-fuzzy signature matching: */

#ifndef MAX_DIST
#  define MAX_DIST          35
#endif /* !MAX_DIST */

/* Detect use-after-free, at the expense of some performance cost: */

#define CHECK_UAF           1

/************************
 * Really obscure stuff *
 ************************/

/* Maximum allocator request size (keep well under INT_MAX): */

#define MAX_ALLOC           0x40000000

/* Percentage of host entries / flows to prune when limits exceeded: */

#define KILL_PERCENT        10

/* PCAP snapshot length: */

#define SNAPLEN             65535

/* Maximum request, response size to keep per flow: */

#define MAX_FLOW_DATA       8192

/* Maximum number of TCP options we will process (< 256): */

#define MAX_TCP_OPT         24

/* Minimum and maximum frequency for timestamp clock (Hz). Note that RFC
   1323 permits 1 - 1000 Hz . At 1000 Hz, the 32-bit counter overflows
   after about 50 days. */

#define MIN_TSCALE          0.7
#define MAX_TSCALE          1500

/* Minimum and maximum interval (ms) for measuring timestamp progrssion. This
   is used to make sure the timestamps are fresh enough to be of any value,
   and that the measurement is not affected by network performance too
   severely. */

#define MIN_TWAIT           25
#define MAX_TWAIT           (1000 * 60 * 10)

/* Time window in which to tolerate timestamps going back slightly or
   otherwise misbehaving during NAT checks (ms): */

#define TSTAMP_GRACE        100

/* Maximum interval between packets used for TS-based NAT checks (ms): */

#define MAX_NAT_TS         (1000 * 60 * 60 * 24)

/* Minimum port drop to serve as a NAT detection signal: */

#define MIN_PORT_DROP       64

/* Threshold before letting NAT detection make a big deal out of TTL change
   for remote hosts (this is to account for peering changes): */

#define SMALL_TTL_CHG       2

/* The distance up to which the system is considered to be local, and therefore
   the SMALL_TTL_CHG threshold should not be taken account: */

#define LOCAL_TTL_LIMIT     5

/* The distance past which the system is considered to be really distant,
   and therefore, changes within SMALL_TTL_CHG should be completely ignored: */

#define NEAR_TTL_LIMIT      9

/* Number of packet scores to keep for NAT detection (< 256): */

#define NAT_SCORES          32

/* Number of hash buckets for p0f.fp signatures: */

#define SIG_BUCKETS         64

/* Number of hash buckets for active connections: */

#define FLOW_BUCKETS        256

/* Number of hash buckets for host data: */

#define HOST_BUCKETS        1024

/* Cache expiration interval (every n packets received): */

#define EXPIRE_INTERVAL     50

/* Non-alphanumeric chars to permit in OS names. This is to allow 'sys' syntax
   to be used unambiguously, yet allow some freedom: */

#define NAME_CHARS " ./-_!?()"

/* Special window size and MSS used by p0f-sendsyn, and detected by p0f: */

#define SPECIAL_MSS         1331
#define SPECIAL_WIN         1337

/* Maximum length of an HTTP URL line we're willing to entertain. The same
   limit is also used for the first line of a response: */

#define HTTP_MAX_URL        1024

/* Maximum number of HTTP headers: */

#define HTTP_MAX_HDRS       32

/* Maximum length of a header name: */

#define HTTP_MAX_HDR_NAME   32

/* Maximum length of a header value: */

#define HTTP_MAX_HDR_VAL    1024

/* Maximum length of a header value for display purposes: */

#define HTTP_MAX_SHOW       200

/* Maximum HTTP 'Date' progression jitter to overlook (s): */

#define HTTP_MAX_DATE_DIFF  10

#ifdef _FROM_FP_HTTP

#include "fp_http.h"

/* Headers that should be tagged as optional by the HTTP fingerprinter in any
   generated signatures: */

static struct http_id req_optional[] = {
  { "Cookie", 0 }, 
  { "Referer", 0 },
  { "Origin", 0 },
  { "Range", 0 },
  { "If-Modified-Since", 0 },
  { "If-None-Match", 0 },
  { "Via", 0 },
  { "X-Forwarded-For", 0 },
  { "Authorization", 0 },
  { "Proxy-Authorization", 0 },
  { "Cache-Control", 0 },
  { 0, 0 }
};

static struct http_id resp_optional[] = {
  { "Set-Cookie", 0 },
  { "Last-Modified", 0 },
  { "ETag", 0 },
  { "Content-Length", 0 },
  { "Content-Disposition", 0 },
  { "Cache-Control", 0 },
  { "Expires", 0 },
  { "Pragma", 0 },
  { "Location", 0 },
  { "Refresh", 0 },
  { "Content-Range", 0 },
  { "Vary", 0 },
  { 0, 0 }
};

/* Common headers that are expected to be present at all times, and deserve
   a special mention if absent in a signature: */

static struct http_id req_common[] = {
  { "Host", 0 },
  { "User-Agent", 0 },
  { "Connection", 0 },
  { "Accept", 0 },
  { "Accept-Encoding", 0 },
  { "Accept-Language", 0 },
  { "Accept-Charset", 0 },
  { "Keep-Alive", 0 },
  { 0, 0 }
};

static struct http_id resp_common[] = {
  { "Content-Type", 0 },
  { "Connection", 0 },
  { "Keep-Alive", 0 },
  { "Accept-Ranges", 0 },
  { "Date", 0 },
  { 0, 0 }
};

/* Headers for which values change depending on the context, and therefore
   should not be included in proposed signatures. This is on top of the
   "optional" header lists, which already implies skipping the value. */

static struct http_id req_skipval[] = {
  { "Host", 0 },
  { "User-Agent", 0 },
  { 0, 0 }
};

static struct http_id resp_skipval[] = {
  { "Date", 0 },
  { "Content-Type", 0 },
  { "Server", 0 },
  { 0, 0 }
};

#endif /* _FROM_FP_HTTP */

#endif /* ! _HAVE_CONFIG_H */
