/*
   p0f - p0f.fp file parser
   ------------------------

   Every project has this one really ugly C file. This is ours.

   Copyright (C) 2012 by Michal Zalewski <lcamtuf@coredump.cx>

   Distributed under the terms and conditions of GNU LGPL.

 */

#define _GNU_SOURCE

#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <ctype.h>

#include <sys/fcntl.h>
#include <netinet/in.h>
#include <sys/types.h>
#include <sys/stat.h>

#include "types.h"
#include "config.h"
#include "debug.h"
#include "alloc-inl.h"
#include "fp_tcp.h"
#include "fp_mtu.h"
#include "fp_http.h"
#include "readfp.h"

static u32 sig_cnt;                     /* Total number of p0f.fp sigs        */

static u8 state = CF_NEED_SECT,         /* Parser state (CF_NEED_*)           */
          mod_type,                     /* Current module (CF_MOD_*)          */
          mod_to_srv,                   /* Traffic direction                  */
          generic;                      /* Generic signature?                 */

static s32 sig_class;                   /* Signature class ID (-1 = userland) */
static u32 sig_name;                    /* Signature name                     */
static u8* sig_flavor;                  /* Signature flavor                   */

static u32* cur_sys;                    /* Current 'sys' values               */
static u32  cur_sys_cnt;                /* Number of 'sys' entries            */

u8 **fp_os_classes,                     /* Map of OS classes                  */
   **fp_os_names;                       /* Map of OS names                    */

static u32 class_cnt,                   /* Sizes for maps                     */
           name_cnt,
           label_id,                    /* Current label ID                   */
           line_no;                     /* Current line number                */


/* Parse 'classes' parameter by populating fp_os_classes. */

static void config_parse_classes(u8* val) {

  while (*val) {

    u8* nxt;

    while (isblank(*val) || *val == ',') val++;

    nxt = val;

    while (isalnum(*nxt)) nxt++;

    if (nxt == val || (*nxt && *nxt != ','))
      FATAL("Malformed class entry in line %u.", line_no);

    fp_os_classes = DFL_ck_realloc(fp_os_classes, (class_cnt + 1) * sizeof(u8*));

    fp_os_classes[class_cnt++] = DFL_ck_memdup_str(val, nxt - val);

    val = nxt;

  }

}


/* Look up or create OS or application id. */

u32 lookup_name_id(u8* name, u8 len) {

  u32 i;

  for (i = 0; i < name_cnt; i++)
    if (!strncasecmp((char*)name, (char*)fp_os_names[i], len)
        && !fp_os_names[i][len]) break;

  if (i == name_cnt) {

    sig_name = name_cnt;

    fp_os_names = DFL_ck_realloc(fp_os_names, (name_cnt + 1) * sizeof(u8*));
    fp_os_names[name_cnt++] = DFL_ck_memdup_str(name, len);

  }

  return i;

}


/* Parse 'label' parameter by looking up ID and recording name / flavor. */

static void config_parse_label(u8* val) {

  u8* nxt;
  u32 i;

  /* Simplified handling for [mtu] signatures. */

  if (mod_type == CF_MOD_MTU) {

    if (!*val) FATAL("Empty MTU label in line %u.\n", line_no);

    sig_flavor  = DFL_ck_strdup(val);
    return;

  }
 
  if (*val == 'g') generic = 1;
  else if (*val == 's') generic = 0;
  else FATAL("Malformed class entry in line %u.", line_no);

  if (val[1] != ':') FATAL("Malformed class entry in line %u.", line_no);

  val += 2;

  nxt = val;
  while (isalnum(*nxt) || *nxt == '!') nxt++;

  if (nxt == val || *nxt != ':') FATAL("Malformed class entry in line %u.", line_no);

  if (*val == '!' && val[1] == ':') {

    sig_class = -1;

  } else {

    *nxt = 0;

    for (i = 0; i < class_cnt; i++)
      if (!strcasecmp((char*)val, (char*)fp_os_classes[i])) break;

    if (i == class_cnt) FATAL("Unknown class '%s' in line %u.", val, line_no);

    sig_class = i;

  }

  nxt++;
  val = nxt;
  while (isalnum(*nxt) || (*nxt && strchr(NAME_CHARS, *nxt))) nxt++;

  if (nxt == val || *nxt != ':') FATAL("Malformed name in line %u.", line_no);

  sig_name = lookup_name_id(val, nxt - val);

  if (nxt[1]) sig_flavor = DFL_ck_strdup(nxt + 1);
    else sig_flavor = NULL;

  label_id++;

}


/* Parse 'sys' parameter into cur_sys[]. */

static void config_parse_sys(u8* val) {

  if (cur_sys) {
    cur_sys = NULL;
    cur_sys_cnt = 0;
  }

  while (*val) {

    u8* nxt;
    u8  is_cl = 0, orig;
    u32 i;

    while (isblank(*val) || *val == ',') val++;

    if (*val == '@') { is_cl = 1; val++; }

    nxt = val;

    while (isalnum(*nxt) || (*nxt && strchr(NAME_CHARS, *nxt))) nxt++;

    if (nxt == val || (*nxt && *nxt != ','))
      FATAL("Malformed sys entry in line %u.", line_no);

    orig = *nxt;
    *nxt = 0;

    if (is_cl) {

      for (i = 0; i < class_cnt; i++)
        if (!strcasecmp((char*)val, (char*)fp_os_classes[i])) break;

      if (i == class_cnt)
        FATAL("Unknown class '%s' in line %u.", val, line_no);

      i |= SYS_CLASS_FLAG;

    } else {

      for (i = 0; i < name_cnt; i++)
        if (!strcasecmp((char*)val, (char*)fp_os_names[i])) break;

      if (i == name_cnt) {
        fp_os_names = DFL_ck_realloc(fp_os_names, (name_cnt + 1) * sizeof(u8*));
        fp_os_names[name_cnt++] = DFL_ck_memdup_str(val, nxt - val);
      }

    }

    cur_sys = DFL_ck_realloc(cur_sys, (cur_sys_cnt + 1) * 4);
    cur_sys[cur_sys_cnt++] = i;

    *nxt = orig;
    val = nxt;

  }

}


/* Read p0f.fp line, dispatching it to fingerprinting modules as necessary. */

static void config_parse_line(u8* line) {

  u8 *val,*eon;

  /* Special handling for [module:direction]... */

  if (*line == '[') {

    u8* dir;

    line++;

    /* Simplified case for [mtu]. */

    if (!strcmp((char*)line, "mtu]")) {

      mod_type = CF_MOD_MTU;
      state = CF_NEED_LABEL;
      return;

    }

    dir = (u8*)strchr((char*)line, ':');

    if (!dir) FATAL("Malformed section identifier in line %u.", line_no);

    *dir = 0; dir++;

    if (!strcmp((char*)line, "tcp")) {

      mod_type = CF_MOD_TCP;

    } else if (!strcmp((char*)line, "http")) {

      mod_type = CF_MOD_HTTP;

    } else {

      FATAL("Unrecognized fingerprinting module '%s' in line %u.", line, line_no);

    }

    if (!strcmp((char*)dir, "request]")) {

      mod_to_srv = 1;

    } else if (!strcmp((char*)dir, "response]")) {

      mod_to_srv = 0;

    } else {

      FATAL("Unrecognized traffic direction in line %u.", line_no);

    }

    state = CF_NEED_LABEL;
    return;

  }

  /* Everything else follows the 'name = value' approach. */

  val = line;

  while (isalpha(*val) || *val == '_') val++;
  eon = val;

  while (isblank(*val)) val++;

  if (line == val || *val != '=')
    FATAL("Unexpected statement in line %u.", line_no);

  while (isblank(*++val));

  *eon = 0;

  if (!strcmp((char*)line, "classes")) {

    if (state != CF_NEED_SECT) 
      FATAL("misplaced 'classes' in line %u.", line_no);

    config_parse_classes(val);

  } else if (!strcmp((char*)line, "ua_os")) {

    if (state != CF_NEED_LABEL || mod_to_srv != 1 || mod_type != CF_MOD_HTTP) 
      FATAL("misplaced 'us_os' in line %u.", line_no);

    http_parse_ua(val, line_no);

  } else if (!strcmp((char*)line, "label")) {

    /* We will drop sig_sys / sig_flavor on the floor if no signatures
       actually created, but it's not worth tracking that. */

    if (state != CF_NEED_LABEL && state != CF_NEED_SIG)
      FATAL("misplaced 'label' in line %u.", line_no);

    config_parse_label(val);

    if (mod_type != CF_MOD_MTU && sig_class < 0) state = CF_NEED_SYS;
    else state = CF_NEED_SIG;

  } else if (!strcmp((char*)line, "sys")) {

    if (state != CF_NEED_SYS)
      FATAL("Misplaced 'sys' in line %u.", line_no);

    config_parse_sys(val);

    state = CF_NEED_SIG;

  } else if (!strcmp((char*)line, "sig")) {

    if (state != CF_NEED_SIG) FATAL("Misplaced 'sig' in line %u.", line_no);

    switch (mod_type) {

      case CF_MOD_TCP:
        tcp_register_sig(mod_to_srv, generic, sig_class, sig_name, sig_flavor,
                         label_id, cur_sys, cur_sys_cnt, val, line_no);
        break;

      case CF_MOD_MTU:
        mtu_register_sig(sig_flavor, val, line_no);
        break;

      case CF_MOD_HTTP:
        http_register_sig(mod_to_srv, generic, sig_class, sig_name, sig_flavor,
                          label_id, cur_sys, cur_sys_cnt, val, line_no);
        break;

    }

    sig_cnt++;

  } else {

    FATAL("Unrecognized field '%s' in line %u.", line, line_no);

  }

}


/* Top-level file parsing. */

void read_config(u8* fname) {

  s32 f;
  struct stat st;
  u8  *data, *cur;

  f = open((char*)fname, O_RDONLY);
  if (f < 0) PFATAL("Cannot open '%s' for reading.", fname);

  if (fstat(f, &st)) PFATAL("fstat() on '%s' failed.", fname);

  if (!st.st_size) { 
    close(f);
    goto end_fp_read;
  }

  cur = data = ck_alloc(st.st_size + 1);

  if (read(f, data, st.st_size) != st.st_size)
    FATAL("Short read from '%s'.", fname);

  data[st.st_size] = 0;

  close(f);

  /* If you put NUL in your p0f.fp... Well, sucks to be you. */

  while (1) {

    u8 *eol;

    line_no++;

    while (isblank(*cur)) cur++;

    eol = cur;
    while (*eol && *eol != '\n') eol++;

    if (*cur != ';' && cur != eol) {

      u8* line = ck_memdup_str(cur, eol - cur);

      config_parse_line(line);

      ck_free(line);

    }

    if (!*eol) break;

    cur = eol + 1;

  }

  ck_free(data);

end_fp_read:  

  if (!sig_cnt)
    SAYF("[!] No signatures found in '%s'.\n", fname);
  else 
    SAYF("[+] Loaded %u signature%s from '%s'.\n", sig_cnt,
         sig_cnt == 1 ? "" : "s", fname);

}

