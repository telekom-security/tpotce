/*
   p0f - type definitions and minor macros
   ---------------------------------------

   Copyright (C) 2012 by Michal Zalewski <lcamtuf@coredump.cx>

   Distributed under the terms and conditions of GNU LGPL.

 */

#ifndef _HAVE_TYPES_H
#define _HAVE_TYPES_H

#include <stdint.h>

typedef uint8_t  u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef uint64_t u64;

typedef int8_t   s8;
typedef int16_t  s16;
typedef int32_t  s32;
typedef int64_t  s64;

#ifndef MIN
#  define MIN(_a,_b) ((_a) > (_b) ? (_b) : (_a))
#  define MAX(_a,_b) ((_a) > (_b) ? (_a) : (_b))
#endif /* !MIN */

/* Macros for non-aligned memory access. */

#ifdef ALIGN_ACCESS
#  include <string.h>
#  define RD16(_val)  ({ u16 _ret; memcpy(&_ret, &(_val), 2); _ret; })
#  define RD32(_val)  ({ u32 _ret; memcpy(&_ret, &(_val), 4); _ret; })
#  define RD16p(_ptr) ({ u16 _ret; memcpy(&_ret, _ptr, 2); _ret; })
#  define RD32p(_ptr) ({ u32 _ret; memcpy(&_ret, _ptr, 4); _ret; })
#else
#  define RD16(_val)  ((u16)_val)
#  define RD32(_val)  ((u32)_val)
#  define RD16p(_ptr) (*((u16*)(_ptr)))
#  define RD32p(_ptr) (*((u32*)(_ptr)))
#endif /* ^ALIGN_ACCESS */

#endif /* ! _HAVE_TYPES_H */
