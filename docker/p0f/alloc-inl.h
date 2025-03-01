/*
   p0f - error-checking, memory-zeroing alloc routines
   ---------------------------------------------------

   Copyright (C) 2012 by Michal Zalewski <lcamtuf@coredump.cx>

   Distributed under the terms and conditions of GNU LGPL.

 */

#ifndef _HAVE_ALLOC_INL_H
#define _HAVE_ALLOC_INL_H

#include <stdlib.h>
#include <string.h>

#include "config.h"
#include "types.h"
#include "debug.h"

#define ALLOC_CHECK_SIZE(_s) do { \
    if ((_s) > MAX_ALLOC) \
      ABORT("Bad alloc request: %u bytes", (_s)); \
  } while (0)

#define ALLOC_CHECK_RESULT(_r,_s) do { \
    if (!(_r)) \
      ABORT("Out of memory: can't allocate %u bytes", (_s)); \
  } while (0)

#define ALLOC_MAGIC   0xFF00
#define ALLOC_MAGIC_F 0xFE00

#define ALLOC_C(_ptr) (((u16*)(_ptr))[-3])
#define ALLOC_S(_ptr) (((u32*)(_ptr))[-1])

#define CHECK_PTR(_p) do { \
    if ((_p) && ALLOC_C(_p) != ALLOC_MAGIC) {\
      if (ALLOC_C(_p) == ALLOC_MAGIC_F) \
        ABORT("Use after free."); \
      else \
        ABORT("Bad alloc canary."); \
    } \
  } while (0)


#define CHECK_PTR_EXPR(_p) ({ \
    typeof (_p) _tmp = (_p); \
    CHECK_PTR(_tmp); \
    _tmp; \
  })

#ifdef CHECK_UAF
#  define CP(_p) CHECK_PTR_EXPR(_p)
#else
#  define CP(_p) (_p)
#endif /* ^CHECK_UAF */

#ifdef ALIGN_ACCESS
#  define ALLOC_OFF 8
#else
#  define ALLOC_OFF 6
#endif /* ^ALIGN_ACCESS */


static inline void* DFL_ck_alloc(u32 size) {
  void* ret;

  if (!size) return NULL;

  ALLOC_CHECK_SIZE(size);
  ret = malloc(size + ALLOC_OFF);
  ALLOC_CHECK_RESULT(ret, size);

  ret += ALLOC_OFF;

  ALLOC_C(ret) = ALLOC_MAGIC;
  ALLOC_S(ret) = size;

  return memset(ret, 0, size);
}


static inline void* DFL_ck_realloc(void* orig, u32 size) {
  void* ret;
  u32   old_size = 0;

  if (!size) {

    if (orig) {

      CHECK_PTR(orig);

      /* Catch pointer issues sooner. */

#ifdef DEBUG_BUILD
      memset(orig - ALLOC_OFF, 0xFF, ALLOC_S(orig) + ALLOC_OFF);
#endif /* DEBUG_BUILD */

      free(orig - ALLOC_OFF);

    }

    return NULL;

  }

  if (orig) {

    CHECK_PTR(orig);

#ifndef DEBUG_BUILD
    ALLOC_C(orig) = ALLOC_MAGIC_F;
#endif /* !DEBUG_BUILD */

    old_size = ALLOC_S(orig);
    orig -= ALLOC_OFF;

    ALLOC_CHECK_SIZE(old_size);

  }

  ALLOC_CHECK_SIZE(size);

#ifndef DEBUG_BUILD

  ret = realloc(orig, size + ALLOC_OFF);
  ALLOC_CHECK_RESULT(ret, size);

#else

  /* Catch pointer issues sooner: force relocation and make sure that the
     original buffer is wiped. */

  ret = malloc(size + ALLOC_OFF);
  ALLOC_CHECK_RESULT(ret, size);

  if (orig) {

    memcpy(ret + ALLOC_OFF, orig + ALLOC_OFF, MIN(size, old_size));
    memset(orig, 0xFF, old_size + ALLOC_OFF);

    ALLOC_C(orig + ALLOC_OFF) = ALLOC_MAGIC_F;

    free(orig);

  }

#endif /* ^!DEBUG_BUILD */

  ret += ALLOC_OFF;

  ALLOC_C(ret) = ALLOC_MAGIC;
  ALLOC_S(ret) = size;

  if (size > old_size)
    memset(ret + old_size, 0, size - old_size);

  return ret;
}


static inline void* DFL_ck_realloc_kb(void* orig, u32 size) {

#ifndef DEBUG_BUILD

  if (orig) {

    CHECK_PTR(orig);

    if (ALLOC_S(orig) >= size) return orig;

    size = ((size >> 10) + 1) << 10;
  }

#endif /* !DEBUG_BUILD */

  return DFL_ck_realloc(orig, size);
}


static inline u8* DFL_ck_strdup(u8* str) {
  void* ret;
  u32   size;

  if (!str) return NULL;

  size = strlen((char*)str) + 1;

  ALLOC_CHECK_SIZE(size);
  ret = malloc(size + ALLOC_OFF);
  ALLOC_CHECK_RESULT(ret, size);

  ret += ALLOC_OFF;

  ALLOC_C(ret) = ALLOC_MAGIC;
  ALLOC_S(ret) = size;

  return memcpy(ret, str, size);
}


static inline void* DFL_ck_memdup(void* mem, u32 size) {
  void* ret;

  if (!mem || !size) return NULL;

  ALLOC_CHECK_SIZE(size);
  ret = malloc(size + ALLOC_OFF);
  ALLOC_CHECK_RESULT(ret, size);
  
  ret += ALLOC_OFF;

  ALLOC_C(ret) = ALLOC_MAGIC;
  ALLOC_S(ret) = size;

  return memcpy(ret, mem, size);
}


static inline u8* DFL_ck_memdup_str(u8* mem, u32 size) {
  u8* ret;

  if (!mem || !size) return NULL;

  ALLOC_CHECK_SIZE(size);
  ret = malloc(size + ALLOC_OFF + 1);
  ALLOC_CHECK_RESULT(ret, size);
  
  ret += ALLOC_OFF;

  ALLOC_C(ret) = ALLOC_MAGIC;
  ALLOC_S(ret) = size;

  memcpy(ret, mem, size);
  ret[size] = 0;

  return ret;
}


static inline void DFL_ck_free(void* mem) {

  if (mem) {

    CHECK_PTR(mem);

#ifdef DEBUG_BUILD

    /* Catch pointer issues sooner. */
    memset(mem - ALLOC_OFF, 0xFF, ALLOC_S(mem) + ALLOC_OFF);

#endif /* DEBUG_BUILD */

    ALLOC_C(mem) = ALLOC_MAGIC_F;

    free(mem - ALLOC_OFF);

  }

}

#ifndef DEBUG_BUILD

/* Non-debugging mode - straightforward aliasing. */

#define ck_alloc        DFL_ck_alloc
#define ck_realloc      DFL_ck_realloc
#define ck_realloc_kb   DFL_ck_realloc_kb
#define ck_strdup       DFL_ck_strdup
#define ck_memdup       DFL_ck_memdup
#define ck_memdup_str   DFL_ck_memdup_str
#define ck_free         DFL_ck_free

#else

/* Debugging mode - include additional structures and support code. */

#define ALLOC_BUCKETS     4096
#define ALLOC_TRK_CHUNK   256

struct TRK_obj {
  void *ptr;
  char *file, *func;
  u32  line;
};


extern struct TRK_obj* TRK[ALLOC_BUCKETS];
extern u32 TRK_cnt[ALLOC_BUCKETS];

#define TRKH(_ptr) (((((u32)(_ptr)) >> 16) ^ ((u32)(_ptr))) % ALLOC_BUCKETS)

/* Adds a new entry to the list of allocated objects. */

static inline void TRK_alloc_buf(void* ptr, const char* file, const char* func,
                                 u32 line) {

  u32 i, bucket;

  if (!ptr) return;

  bucket = TRKH(ptr);

  for (i = 0; i < TRK_cnt[bucket]; i++)

    if (!TRK[bucket][i].ptr) {

      TRK[bucket][i].ptr  = ptr;
      TRK[bucket][i].file = (char*)file;
      TRK[bucket][i].func = (char*)func;
      TRK[bucket][i].line = line;
      return;

    }

  /* No space available. */

  if (!(i % ALLOC_TRK_CHUNK)) {

    TRK[bucket] = DFL_ck_realloc(TRK[bucket],
      (TRK_cnt[bucket] + ALLOC_TRK_CHUNK) * sizeof(struct TRK_obj));

  }

  TRK[bucket][i].ptr  = ptr;
  TRK[bucket][i].file = (char*)file;
  TRK[bucket][i].func = (char*)func;
  TRK[bucket][i].line = line;

  TRK_cnt[bucket]++;

}


/* Removes entry from the list of allocated objects. */

static inline void TRK_free_buf(void* ptr, const char* file, const char* func,
                                u32 line) {

  u32 i, bucket;

  if (!ptr) return;

  bucket = TRKH(ptr);

  for (i = 0; i < TRK_cnt[bucket]; i++)

    if (TRK[bucket][i].ptr == ptr) {

      TRK[bucket][i].ptr = 0;
      return;

    }

  WARN("ALLOC: Attempt to free non-allocated memory in %s (%s:%u)",
       func, file, line);

}


/* Does a final report on all non-deallocated objects. */

static inline void TRK_report(void) {

  u32 i, bucket;

  fflush(0);

  for (bucket = 0; bucket < ALLOC_BUCKETS; bucket++)
    for (i = 0; i < TRK_cnt[bucket]; i++)
      if (TRK[bucket][i].ptr)
        WARN("ALLOC: Memory never freed, created in %s (%s:%u)",
             TRK[bucket][i].func, TRK[bucket][i].file, TRK[bucket][i].line);

}


/* Simple wrappers for non-debugging functions: */

static inline void* TRK_ck_alloc(u32 size, const char* file, const char* func,
                                 u32 line) {

  void* ret = DFL_ck_alloc(size);
  TRK_alloc_buf(ret, file, func, line);
  return ret;

}


static inline void* TRK_ck_realloc(void* orig, u32 size, const char* file,
                                   const char* func, u32 line) {

  void* ret = DFL_ck_realloc(orig, size);
  TRK_free_buf(orig, file, func, line);
  TRK_alloc_buf(ret, file, func, line);
  return ret;

}


static inline void* TRK_ck_realloc_kb(void* orig, u32 size, const char* file,
                                      const char* func, u32 line) {

  void* ret = DFL_ck_realloc_kb(orig, size);
  TRK_free_buf(orig, file, func, line);
  TRK_alloc_buf(ret, file, func, line);
  return ret;

}


static inline void* TRK_ck_strdup(u8* str, const char* file, const char* func,
                                  u32 line) {

  void* ret = DFL_ck_strdup(str);
  TRK_alloc_buf(ret, file, func, line);
  return ret;

}


static inline void* TRK_ck_memdup(void* mem, u32 size, const char* file,
                                  const char* func, u32 line) {

  void* ret = DFL_ck_memdup(mem, size);
  TRK_alloc_buf(ret, file, func, line);
  return ret;

}


static inline void* TRK_ck_memdup_str(void* mem, u32 size, const char* file,
                                      const char* func, u32 line) {

  void* ret = DFL_ck_memdup_str(mem, size);
  TRK_alloc_buf(ret, file, func, line);
  return ret;

}


static inline void TRK_ck_free(void* ptr, const char* file,
                                const char* func, u32 line) {

  TRK_free_buf(ptr, file, func, line);
  DFL_ck_free(ptr);

}

/* Alias user-facing names to tracking functions: */

#define ck_alloc(_p1) \
  TRK_ck_alloc(_p1, __FILE__, __FUNCTION__, __LINE__)

#define ck_realloc(_p1, _p2) \
  TRK_ck_realloc(_p1, _p2, __FILE__, __FUNCTION__, __LINE__)

#define ck_realloc_kb(_p1, _p2) \
  TRK_ck_realloc_kb(_p1, _p2, __FILE__, __FUNCTION__, __LINE__)

#define ck_strdup(_p1) \
  TRK_ck_strdup(_p1, __FILE__, __FUNCTION__, __LINE__)

#define ck_memdup(_p1, _p2) \
  TRK_ck_memdup(_p1, _p2, __FILE__, __FUNCTION__, __LINE__)

#define ck_memdup_str(_p1, _p2) \
  TRK_ck_memdup_str(_p1, _p2, __FILE__, __FUNCTION__, __LINE__)

#define ck_free(_p1) \
  TRK_ck_free(_p1, __FILE__, __FUNCTION__, __LINE__)

#endif /* ^!DEBUG_BUILD */

#define alloc_printf(_str...) ({ \
    u8* _tmp; \
    s32 _len = snprintf(NULL, 0, _str); \
    if (_len < 0) FATAL("Whoa, snprintf() fails?!"); \
    _tmp = ck_alloc(_len + 1); \
    snprintf((char*)_tmp, _len + 1, _str); \
    _tmp; \
  })

#endif /* ! _HAVE_ALLOC_INL_H */
