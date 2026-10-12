/*
   LZ4 - Fast LZ compression algorithm
   Copyright (C) 2011-2023, Yann Collet.

   BSD 2-Clause License (http://www.opensource.org/licenses/bsd-license.php)

   Redistribution and use in source and binary forms, with or without
   modification, are permitted provided that the following conditions are
   met:

       * Redistributions of source code must retain the above copyright
   notice, this list of conditions and the following disclaimer.
       * Redistributions in binary form must reproduce the above
   copyright notice, this list of conditions and the following disclaimer
   in the documentation and/or other materials provided with the
   distribution.

   THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
   "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
   LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
   A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
   OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
   SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
   LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
   DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
   THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
   (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
   OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

   You can contact the author at :
    - LZ4 homepage : http://www.lz4.org
    - LZ4 source repository : https://github.com/lz4/lz4
*/

/*-************************************
*  Tuning parameters
**************************************/
/*
 * LZ4_HEAPMODE :
 * Select how stateless compression functions like `LZ4_compress_default()`
 * allocate memory for their hash table,
 * in memory stack (0:default, fastest), or in memory heap (1:requires malloc()).
 */
#ifndef LZ4_HEAPMODE
#  define LZ4_HEAPMODE 0
#endif

/*
 * LZ4_ACCELERATION_DEFAULT :
 * Select "acceleration" for LZ4_compress_fast() when parameter value <= 0
 */
#define LZ4_ACCELERATION_DEFAULT 1
/*
 * LZ4_ACCELERATION_MAX :
 * Any "acceleration" value higher than this threshold
 * get treated as LZ4_ACCELERATION_MAX instead (fix #876)
 */
#define LZ4_ACCELERATION_MAX 65537


/*-************************************
*  CPU Feature Detection
**************************************/
/* LZ4_FORCE_MEMORY_ACCESS
 * By default, access to unaligned memory is controlled by `memcpy()`, which is safe and portable.
 * Unfortunately, on some target/compiler combinations, the generated assembly is sub-optimal.
 * The below switch allow to select different access method for improved performance.
 * Method 0 (default) : use `memcpy()`. Safe and portable.
 * Method 1 : `__packed` statement. It depends on compiler extension (ie, not portable).
 *            This method is safe if your compiler supports it, and *generally* as fast or faster than `memcpy`.
 * Method 2 : direct access. This method is portable but violate C standard.
 *            It can generate buggy code on targets which assembly generation depends on alignment.
 *            But in some circumstances, it's the only known way to get the most performance (ie GCC + ARMv6)
 * See https://fastcompression.blogspot.fr/2015/08/accessing-unaligned-memory.html for details.
 * Prefer these methods in priority order (0 > 1 > 2)
 */
#ifndef LZ4_FORCE_MEMORY_ACCESS   /* can be defined externally */
#  if defined(__GNUC__) && \
  ( defined(__ARM_ARCH_6__) || defined(__ARM_ARCH_6J__) || defined(__ARM_ARCH_6K__) \
  || defined(__ARM_ARCH_6Z__) || defined(__ARM_ARCH_6ZK__) || defined(__ARM_ARCH_6T2__) )
#    define LZ4_FORCE_MEMORY_ACCESS 2
#  elif (defined(__INTEL_COMPILER) && !defined(_WIN32)) || defined(__GNUC__) || defined(_MSC_VER)
#    define LZ4_FORCE_MEMORY_ACCESS 1
#  endif
#endif

/*
 * LZ4_FORCE_SW_BITCOUNT
 * Define this parameter if your target system or compiler does not support hardware bit count
 */
#if defined(_MSC_VER) && defined(_WIN32_WCE)   /* Visual Studio for WinCE doesn't support Hardware bit count */
#  undef  LZ4_FORCE_SW_BITCOUNT  /* avoid double def */
#  define LZ4_FORCE_SW_BITCOUNT
#endif



/*-************************************
*  Dependency
**************************************/
/*
 * LZ4_SRC_INCLUDED:
 * Amalgamation flag, whether lz4.c is included
 */
#ifndef LZ4_SRC_INCLUDED
#  define LZ4_SRC_INCLUDED 1
#endif

#ifndef LZ4_DISABLE_DEPRECATE_WARNINGS
#  define LZ4_DISABLE_DEPRECATE_WARNINGS /* due to LZ4_decompress_safe_withPrefix64k */
#endif

#ifndef LZ4_STATIC_LINKING_ONLY
#  define LZ4_STATIC_LINKING_ONLY
#endif
#include "lz4.h"
/* see also "memory routines" below */


/*-************************************
*  Compiler Options
**************************************/
#if defined(_MSC_VER) && (_MSC_VER >= 1400)  /* Visual Studio 2005+ */
#  include <intrin.h>               /* only present in VS2005+ */
#  pragma warning(disable : 4127)   /* disable: C4127: conditional expression is constant */
#  pragma warning(disable : 6237)   /* disable: C6237: conditional expression is always 0 */
#  pragma warning(disable : 6239)   /* disable: C6239: (<non-zero constant> && <expression>) always evaluates to the result of <expression> */
#  pragma warning(disable : 6240)   /* disable: C6240: (<expression> && <non-zero constant>) always evaluates to the result of <expression> */
#  pragma warning(disable : 6326)   /* disable: C6326: Potential comparison of a constant with another constant */
#endif  /* _MSC_VER */

#ifndef LZ4_FORCE_INLINE
#  if defined (_MSC_VER) && !defined (__clang__)    /* MSVC */
#    define LZ4_FORCE_INLINE static __forceinline
#  else
#    if defined (__cplusplus) || defined (__STDC_VERSION__) && __STDC_VERSION__ >= 199901L   /* C99 */
#      if defined (__GNUC__) || defined (__clang__)
#        define LZ4_FORCE_INLINE static inline __attribute__((always_inline))
#      else
#        define LZ4_FORCE_INLINE static inline
#      endif
#    else
#      define LZ4_FORCE_INLINE static
#    endif /* __STDC_VERSION__ */
#  endif  /* _MSC_VER */
#endif /* LZ4_FORCE_INLINE */

/* LZ4_FORCE_O2 and LZ4_FORCE_INLINE
 * gcc on ppc64le generates an unrolled SIMDized loop for LZ4_wildCopy8,
 * together with a simple 8-byte copy loop as a fall-back path.
 * However, this optimization hurts the decompression speed by >30%,
 * because the execution does not go to the optimized loop
 * for typical compressible data, and all of the preamble checks
 * before going to the fall-back path become useless overhead.
 * This optimization happens only with the -O3 flag, and -O2 generates
 * a simple 8-byte copy loop.
 * With gcc on ppc64le, all of the LZ4_decompress_* and LZ4_wildCopy8
 * functions are annotated with __attribute__((optimize("O2"))),
 * and also LZ4_wildCopy8 is forcibly inlined, so that the O2 attribute
 * of LZ4_wildCopy8 does not affect the compression speed.
 */
#if defined(__PPC64__) && defined(__LITTLE_ENDIAN__) && defined(__GNUC__) && !defined(__clang__)
#  define LZ4_FORCE_O2  __attribute__((optimize("O2")))
#  undef LZ4_FORCE_INLINE
#  define LZ4_FORCE_INLINE  static __inline __attribute__((optimize("O2"),always_inline))
#else
#  define LZ4_FORCE_O2
#endif

#if (defined(__GNUC__) && (__GNUC__ >= 3)) || (defined(__INTEL_COMPILER) && (__INTEL_COMPILER >= 800)) || defined(__clang__)
#  define expect(expr,value)    (__builtin_expect ((expr),(value)) )
#else
#  define expect(expr,value)    (expr)
#endif

#ifndef likely
#define likely(expr)     expect((expr) != 0, 1)
#endif
#ifndef unlikely
#define unlikely(expr)   expect((expr) != 0, 0)
#endif

/* Should the alignment test prove unreliable, for some reason,
 * it can be disabled by setting LZ4_ALIGN_TEST to 0 */
#ifndef LZ4_ALIGN_TEST  /* can be externally provided */
# define LZ4_ALIGN_TEST 1
#endif


/*-************************************
*  Memory routines
**************************************/

/*! LZ4_STATIC_LINKING_ONLY_DISABLE_MEMORY_ALLOCATION :
 *  Disable relatively high-level LZ4/HC functions that use dynamic memory
 *  allocation functions (malloc(), calloc(), free()).
 *
 *  Note that this is a compile-time switch. And since it disables
 *  public/stable LZ4 v1 API functions, we don't recommend using this
 *  symbol to generate a library for distribution.
 *
 *  The following public functions are removed when this symbol is defined.
 *  - lz4   : LZ4_createStream, LZ4_freeStream,
 *            LZ4_createStreamDecode, LZ4_freeStreamDecode, LZ4_create (deprecated)
 *  - lz4hc : LZ4_createStreamHC, LZ4_freeStreamHC,
 *            LZ4_createHC (deprecated), LZ4_freeHC  (deprecated)
 *  - lz4frame, lz4file : All LZ4F_* functions
 */
#if defined(LZ4_STATIC_LINKING_ONLY_DISABLE_MEMORY_ALLOCATION)
#  define ALLOC(s)          lz4_error_memory_allocation_is_disabled
#  define ALLOC_AND_ZERO(s) lz4_error_memory_allocation_is_disabled
#  define FREEMEM(p)        lz4_error_memory_allocation_is_disabled
#elif defined(LZ4_USER_MEMORY_FUNCTIONS)
/* memory management functions can be customized by user project.
 * Below functions must exist somewhere in the Project
 * and be available at link time */
void* LZ4_malloc(size_t s);
void* LZ4_calloc(size_t n, size_t s);
void  LZ4_free(void* p);
# define ALLOC(s)          LZ4_malloc(s)
# define ALLOC_AND_ZERO(s) LZ4_calloc(1,s)
# define FREEMEM(p)        LZ4_free(p)
#else
# include <stdlib.h>   /* malloc, calloc, free */
# define ALLOC(s)          malloc(s)
# define ALLOC_AND_ZERO(s) calloc(1,s)
# define FREEMEM(p)        free(p)
#endif

#if ! LZ4_FREESTANDING
#  include <string.h>   /* memset, memcpy */
#endif
#if !defined(LZ4_memset)
#  define LZ4_memset(p,v,s) memset((p),(v),(s))
#endif
#define MEM_INIT(p,v,s)   LZ4_memset((p),(v),(s))


/*-************************************
*  Common Constants
**************************************/
#define MINMATCH 4

#define WILDCOPYLENGTH 8
#define LASTLITERALS   5   /* see ../doc/lz4_Block_format.md#parsing-restrictions */
#define MFLIMIT       12   /* see ../doc/lz4_Block_format.md#parsing-restrictions */
#define MATCH_SAFEGUARD_DISTANCE  ((2*WILDCOPYLENGTH) - MINMATCH)   /* ensure it's possible to write 2 x wildcopyLength without overflowing output buffer */
#define FASTLOOP_SAFE_DISTANCE 64
static const int LZ4_minLength = (MFLIMIT+1);

#define KB *(1 <<10)
#define MB *(1 <<20)
#define GB *(1U<<30)

#define LZ4_DISTANCE_ABSOLUTE_MAX 65535
#if (LZ4_DISTANCE_MAX > LZ4_DISTANCE_ABSOLUTE_MAX)   /* max supported by LZ4 format */
#  error "LZ4_DISTANCE_MAX is too big : must be <= 65535"
#endif

#define ML_BITS  4
#define ML_MASK  ((1U<<ML_BITS)-1)
#define RUN_BITS (8-ML_BITS)
#define RUN_MASK ((1U<<RUN_BITS)-1)


/*-************************************
*  Error detection
**************************************/
#if defined(LZ4_DEBUG) && (LZ4_DEBUG>=1)
#  include <assert.h>
#else
#  ifndef assert
#    define assert(condition) ((void)0)
#  endif
#endif

#define LZ4_STATIC_ASSERT(c)   { enum { LZ4_static_assert = 1/(int)(!!(c)) }; }   /* use after variable declarations */

#if defined(LZ4_DEBUG) && (LZ4_DEBUG>=2)
#  include <stdio.h>
   static int g_debuglog_enable = 1;
#  define DEBUGLOG(l, ...) {                          \
        if ((g_debuglog_enable) && (l<=LZ4_DEBUG)) {  \
            fprintf(stderr, __FILE__  " %i: ", __LINE__); \
            fprintf(stderr, __VA_ARGS__);             \
            fprintf(stderr, " \n");                   \
    }   }
#else
#  define DEBUGLOG(l, ...) {}    /* disabled */
#endif

static int LZ4_isAligned(const void* ptr, size_t alignment)
__CPROVER_requires(alignment >= 1)
__CPROVER_assigns()
__CPROVER_ensures(__CPROVER_return_value == (((size_t)ptr & (alignment - 1)) == 0))
{
    return ((size_t)ptr & (alignment -1)) == 0;
}


/*-************************************
*  Types
**************************************/
#include <limits.h>
#if defined(__cplusplus) || (defined (__STDC_VERSION__) && (__STDC_VERSION__ >= 199901L) /* C99 */)
# include <stdint.h>
  typedef  uint8_t BYTE;
  typedef uint16_t U16;
  typedef uint32_t U32;
  typedef  int32_t S32;
  typedef uint64_t U64;
  typedef uintptr_t uptrval;
#else
# if UINT_MAX != 4294967295UL
#   error "LZ4 code (when not C++ or C99) assumes that sizeof(int) == 4"
# endif
  typedef unsigned char       BYTE;
  typedef unsigned short      U16;
  typedef unsigned int        U32;
  typedef   signed int        S32;
  typedef unsigned long long  U64;
  typedef size_t              uptrval;   /* generally true, except OpenVMS-64 */
#endif

#if defined(__x86_64__)
  typedef U64    reg_t;   /* 64-bits in x32 mode */
#else
  typedef size_t reg_t;   /* 32-bits in x32 mode */
#endif

typedef enum {
    notLimited = 0,
    limitedOutput = 1,
    fillOutput = 2
} limitedOutput_directive;


/*-************************************
*  Reading and writing into memory
**************************************/

/**
 * LZ4 relies on memcpy with a constant size being inlined. In freestanding
 * environments, the compiler can't assume the implementation of memcpy() is
 * standard compliant, so it can't apply its specialized memcpy() inlining
 * logic. When possible, use __builtin_memcpy() to tell the compiler to analyze
 * memcpy() as if it were standard compliant, so it can inline it in freestanding
 * environments. This is needed when decompressing the Linux Kernel, for example.
 */
#if !defined(LZ4_memcpy)
#  if defined(__GNUC__) && (__GNUC__ >= 4)
#    define LZ4_memcpy(dst, src, size) __builtin_memcpy(dst, src, size)
#  else
#    define LZ4_memcpy(dst, src, size) memcpy(dst, src, size)
#  endif
#endif

#if !defined(LZ4_memmove)
#  if defined(__GNUC__) && (__GNUC__ >= 4)
#    define LZ4_memmove __builtin_memmove
#  else
#    define LZ4_memmove memmove
#  endif
#endif

static unsigned LZ4_isLittleEndian(void)
__CPROVER_ensures(__CPROVER_return_value == 0 || __CPROVER_return_value == 1)
{
    const union { U32 u; BYTE c[4]; } one = { 1 };   /* don't use static : performance detrimental */
    return one.c[0];
}

#if defined(__GNUC__) || defined(__INTEL_COMPILER)
#define LZ4_PACK( __Declaration__ ) __Declaration__ __attribute__((__packed__))
#elif defined(_MSC_VER)
#define LZ4_PACK( __Declaration__ ) __pragma( pack(push, 1) ) __Declaration__ __pragma( pack(pop))
#endif

#if defined(LZ4_FORCE_MEMORY_ACCESS) && (LZ4_FORCE_MEMORY_ACCESS==2)
/* lie to the compiler about data alignment; use with caution */

static U16 LZ4_read16(const void* memPtr) { return *(const U16*) memPtr; }
static U32 LZ4_read32(const void* memPtr) { return *(const U32*) memPtr; }
static reg_t LZ4_read_ARCH(const void* memPtr)
__CPROVER_requires(__CPROVER_r_ok(memPtr, sizeof(reg_t)))
__CPROVER_ensures(__CPROVER_return_value == *(const reg_t *)memPtr)
{ return *(const reg_t*) memPtr; }

static void LZ4_write16(void* memPtr, U16 value) { *(U16*)memPtr = value; }
static void LZ4_write32(void* memPtr, U32 value) { *(U32*)memPtr = value; }

#elif defined(LZ4_FORCE_MEMORY_ACCESS) && (LZ4_FORCE_MEMORY_ACCESS==1)

/* __pack instructions are safer, but compiler specific, hence potentially problematic for some compilers */
/* currently only defined for gcc and icc */
LZ4_PACK(typedef struct { U16 u16; }) LZ4_unalign16;
LZ4_PACK(typedef struct { U32 u32; }) LZ4_unalign32;
LZ4_PACK(typedef struct { reg_t uArch; }) LZ4_unalignST;

static U16 LZ4_read16(const void* ptr)
__CPROVER_requires(__CPROVER_r_ok(ptr, sizeof(U16)))
__CPROVER_assigns()
__CPROVER_ensures(__CPROVER_return_value == *(const U16*)ptr)
{ return ((const LZ4_unalign16*)ptr)->u16; }
static U32 LZ4_read32(const void* ptr)
__CPROVER_requires(__CPROVER_r_ok(ptr, sizeof(U32)))
__CPROVER_assigns()
__CPROVER_ensures(__CPROVER_return_value == ((const LZ4_unalign32*)ptr)->u32)
{ return ((const LZ4_unalign32*)ptr)->u32; }
static reg_t LZ4_read_ARCH(const void* ptr)
__CPROVER_requires(__CPROVER_r_ok(ptr, sizeof(reg_t)))
__CPROVER_ensures(__CPROVER_return_value == ((const LZ4_unalignST*)ptr)->uArch)
{ return ((const LZ4_unalignST*)ptr)->uArch; }

static void LZ4_write16(void* memPtr, U16 value)
__CPROVER_requires(__CPROVER_w_ok(memPtr, sizeof(U16)))
__CPROVER_assigns(((LZ4_unalign16*)memPtr)->u16)
__CPROVER_ensures(((LZ4_unalign16*)memPtr)->u16 == value)
{ ((LZ4_unalign16*)memPtr)->u16 = value; }
static void LZ4_write32(void* memPtr, U32 value)
__CPROVER_requires(__CPROVER_w_ok(memPtr, sizeof(U32)))
__CPROVER_assigns(((LZ4_unalign32*)memPtr)->u32)
__CPROVER_ensures(((LZ4_unalign32*)memPtr)->u32 == value)
{ ((LZ4_unalign32*)memPtr)->u32 = value; }

#else  /* safe and portable access using memcpy() */

static U16 LZ4_read16(const void* memPtr)
__CPROVER_requires(__CPROVER_r_ok(memPtr, sizeof(U16)))
__CPROVER_assigns()
__CPROVER_ensures(__CPROVER_return_value == *(const U16*)memPtr)
{
    U16 val; LZ4_memcpy(&val, memPtr, sizeof(val)); return val;
}

static U32 LZ4_read32(const void* memPtr)
{
    U32 val; LZ4_memcpy(&val, memPtr, sizeof(val)); return val;
}

static reg_t LZ4_read_ARCH(const void* memPtr)
__CPROVER_requires(__CPROVER_r_ok(memPtr, sizeof(reg_t)))
__CPROVER_ensures(__CPROVER_return_value == *(const reg_t *)memPtr)
{
    reg_t val; LZ4_memcpy(&val, memPtr, sizeof(val)); return val;
}

static void LZ4_write16(void* memPtr, U16 value)
{
    LZ4_memcpy(memPtr, &value, sizeof(value));
}

static void LZ4_write32(void* memPtr, U32 value)
{
    LZ4_memcpy(memPtr, &value, sizeof(value));
}

#endif /* LZ4_FORCE_MEMORY_ACCESS */


static U16 LZ4_readLE16(const void* memPtr)
__CPROVER_requires(__CPROVER_r_ok(memPtr, sizeof(U16)))
__CPROVER_assigns()
__CPROVER_ensures(__CPROVER_return_value == (U16)((U16)((const BYTE*)memPtr)[0] | (((const BYTE*)memPtr)[1] << 8)))
{
    if (LZ4_isLittleEndian()) {
        return LZ4_read16(memPtr);
    } else {
        const BYTE* p = (const BYTE*)memPtr;
        return (U16)((U16)p[0] | (p[1]<<8));
    }
}

#ifdef LZ4_STATIC_LINKING_ONLY_ENDIANNESS_INDEPENDENT_OUTPUT
static U32 LZ4_readLE32(const void* memPtr)
{
    if (LZ4_isLittleEndian()) {
        return LZ4_read32(memPtr);
    } else {
        const BYTE* p = (const BYTE*)memPtr;
        return (U32)p[0] | (p[1]<<8) | (p[2]<<16) | (p[3]<<24);
    }
}
#endif

static void LZ4_writeLE16(void* memPtr, U16 value)
__CPROVER_requires(__CPROVER_w_ok(memPtr, sizeof(U16)))
__CPROVER_assigns(__CPROVER_object_upto(memPtr, sizeof(U16)))
__CPROVER_ensures(((BYTE*)memPtr)[0] == (BYTE)value)
__CPROVER_ensures(((BYTE*)memPtr)[1] == (BYTE)(value >> 8))
{
    if (LZ4_isLittleEndian()) {
        LZ4_write16(memPtr, value);
    } else {
        BYTE* p = (BYTE*)memPtr;
        p[0] = (BYTE) value;
        p[1] = (BYTE)(value>>8);
    }
}

/* Number of bytes LZ4_wildCopy8 writes: it copies 8 bytes at a time in a
 * do-while loop until the destination pointer reaches dstEnd, so it always
 * writes at least 8 bytes and rounds the gap up to the next multiple of 8. */
#define AVOCADO_SPEC_WC8_DIST(d, e) \
    ((size_t)(__CPROVER_POINTER_OFFSET(e) - __CPROVER_POINTER_OFFSET(d)))
#define AVOCADO_SPEC_WC8_LEN(d, e)  (8 + 8 * ((AVOCADO_SPEC_WC8_DIST(d, e) - 1) / 8))

/* customized variant of memcpy, which can overwrite up to 8 bytes beyond dstEnd */
LZ4_FORCE_INLINE
void LZ4_wildCopy8(void* dstPtr, const void* srcPtr, void* dstEnd)
__CPROVER_requires(__CPROVER_same_object(dstPtr, dstEnd))
__CPROVER_requires(__CPROVER_POINTER_OFFSET(dstPtr) < __CPROVER_POINTER_OFFSET(dstEnd))
__CPROVER_requires(AVOCADO_SPEC_WC8_DIST(dstPtr, dstEnd) <= 56)
__CPROVER_requires(__CPROVER_w_ok(dstPtr, AVOCADO_SPEC_WC8_LEN(dstPtr, dstEnd)))
__CPROVER_requires(__CPROVER_r_ok(srcPtr, AVOCADO_SPEC_WC8_LEN(dstPtr, dstEnd)))
__CPROVER_requires(
    __CPROVER_same_object(srcPtr, dstPtr) ==>
    ( __CPROVER_POINTER_OFFSET(srcPtr) + (long)AVOCADO_SPEC_WC8_LEN(dstPtr, dstEnd) <= __CPROVER_POINTER_OFFSET(dstPtr)
      || __CPROVER_POINTER_OFFSET(dstPtr) + (long)AVOCADO_SPEC_WC8_LEN(dstPtr, dstEnd) <= __CPROVER_POINTER_OFFSET(srcPtr) ) )
__CPROVER_assigns(__CPROVER_object_upto(dstPtr, AVOCADO_SPEC_WC8_LEN(dstPtr, dstEnd)))
__CPROVER_ensures(__CPROVER_forall {
    size_t avocado_spec_i;
    (avocado_spec_i < AVOCADO_SPEC_WC8_LEN(dstPtr, dstEnd)) ==>
        ((BYTE*)dstPtr)[avocado_spec_i] == ((const BYTE*)srcPtr)[avocado_spec_i]
})
{
    BYTE* d = (BYTE*)dstPtr;
    const BYTE* s = (const BYTE*)srcPtr;
    BYTE* const e = (BYTE*)dstEnd;

    do { LZ4_memcpy(d,s,8); d+=8; s+=8; } while (d<e);
}

static const unsigned inc32table[8] = {0, 1, 2,  1,  0,  4, 4, 4};
static const int      dec64table[8] = {0, 0, 0, -1, -4,  1, 2, 3};


#ifndef LZ4_FAST_DEC_LOOP
#  if defined __i386__ || defined _M_IX86 || defined __x86_64__ || defined _M_X64
#    define LZ4_FAST_DEC_LOOP 1
#  elif defined(__aarch64__) && defined(__APPLE__)
#    define LZ4_FAST_DEC_LOOP 1
#  elif defined(__aarch64__) && !defined(__clang__)
     /* On non-Apple aarch64, we disable this optimization for clang because
      * on certain mobile chipsets, performance is reduced with clang. For
      * more information refer to https://github.com/lz4/lz4/pull/707 */
#    define LZ4_FAST_DEC_LOOP 1
#  else
#    define LZ4_FAST_DEC_LOOP 0
#  endif
#endif

#if LZ4_FAST_DEC_LOOP

LZ4_FORCE_INLINE void
LZ4_memcpy_using_offset_base(BYTE* dstPtr, const BYTE* srcPtr, BYTE* dstEnd, const size_t offset)
__CPROVER_requires(offset >= 1 && offset <= 16)
/* src and dst live in ONE object (dstPtr == srcPtr + offset); allocate it via a
 * single is_fresh on srcPtr and derive dstPtr by arithmetic, so the overlapping
 * pointers are backed by a concrete shared object (two independent is_fresh /
 * r_ok pointers would be unsatisfiable here and make the proof vacuous). */
__CPROVER_requires(__CPROVER_is_fresh(srcPtr, 48))
__CPROVER_requires(srcPtr + offset == dstPtr)
__CPROVER_requires(__CPROVER_same_object(dstPtr, dstEnd))
__CPROVER_requires(__CPROVER_POINTER_OFFSET(dstEnd) - __CPROVER_POINTER_OFFSET(dstPtr) >= 9)
__CPROVER_requires(__CPROVER_POINTER_OFFSET(dstEnd) - __CPROVER_POINTER_OFFSET(dstPtr) <= 16)
__CPROVER_assigns(__CPROVER_object_upto(dstPtr, 16))
__CPROVER_ensures(__CPROVER_forall {
    size_t avocado_spec_i;
    (avocado_spec_i < 16) ==>
        (dstPtr[avocado_spec_i] == srcPtr[avocado_spec_i % offset])
})
{
    assert(srcPtr + offset == dstPtr);
    if (offset < 8) {
        LZ4_write32(dstPtr, 0);   /* silence an msan warning when offset==0 */
        dstPtr[0] = srcPtr[0];
        dstPtr[1] = srcPtr[1];
        dstPtr[2] = srcPtr[2];
        dstPtr[3] = srcPtr[3];
        srcPtr += inc32table[offset];
        LZ4_memcpy(dstPtr+4, srcPtr, 4);
        srcPtr -= dec64table[offset];
        dstPtr += 8;
    } else {
        LZ4_memcpy(dstPtr, srcPtr, 8);
        dstPtr += 8;
        srcPtr += 8;
    }

    LZ4_wildCopy8(dstPtr, srcPtr, dstEnd);
}

/* Fixed capacity (bytes) modelled for the dst/src objects in the contract of
 * LZ4_wildCopy32; keeps the copy loop fully unrolled and every mutant decided. */
#define AVOCADO_SPEC_WC32_CAP 64u
/* Distance requested by the caller, dstEnd - dstPtr, in bytes. */
#define AVOCADO_SPEC_WC32_LEN ((size_t)((const unsigned char*)dstEnd - (const unsigned char*)dstPtr))
/* Bytes actually written: LEN rounded up to the next multiple of 32 (the loop
 * runs ceil(LEN/32) iterations, each writing exactly 32 bytes). */
#define AVOCADO_SPEC_WC32_WRITTEN ((((AVOCADO_SPEC_WC32_LEN) + 31u) / 32u) * 32u)

/* customized variant of memcpy, which can overwrite up to 32 bytes beyond dstEnd
 * this version copies two times 16 bytes (instead of one time 32 bytes)
 * because it must be compatible with offsets >= 16. */
LZ4_FORCE_INLINE void
LZ4_wildCopy32(void* dstPtr, const void* srcPtr, void* dstEnd)
__CPROVER_requires(__CPROVER_is_fresh(dstPtr, AVOCADO_SPEC_WC32_CAP))
__CPROVER_requires(__CPROVER_is_fresh(srcPtr, AVOCADO_SPEC_WC32_CAP))
__CPROVER_requires(__CPROVER_same_object(dstPtr, dstEnd))
__CPROVER_requires(AVOCADO_SPEC_WC32_LEN >= 1 && AVOCADO_SPEC_WC32_WRITTEN <= AVOCADO_SPEC_WC32_CAP)
__CPROVER_assigns(__CPROVER_object_upto(dstPtr, AVOCADO_SPEC_WC32_WRITTEN))
__CPROVER_ensures(__CPROVER_forall {
    size_t i;
    (i < AVOCADO_SPEC_WC32_WRITTEN) ==>
        (((unsigned char*)dstPtr)[i] == ((const unsigned char*)srcPtr)[i])
})
{
    BYTE* d = (BYTE*)dstPtr;
    const BYTE* s = (const BYTE*)srcPtr;
    BYTE* const e = (BYTE*)dstEnd;

    do { LZ4_memcpy(d,s,16); LZ4_memcpy(d+16,s+16,16); d+=32; s+=32; } while (d<e);
}

/* LZ4_memcpy_using_offset()  presumes :
 * - dstEnd >= dstPtr + MINMATCH
 * - there is at least 12 bytes available to write after dstEnd */
LZ4_FORCE_INLINE void
LZ4_memcpy_using_offset(BYTE* dstPtr, const BYTE* srcPtr, BYTE* dstEnd, const size_t offset)
/* Same overlapping layout as the base helper: src and dst live in ONE object
 * (dstPtr == srcPtr + offset), allocated via a single is_fresh on srcPtr with
 * dstPtr derived by arithmetic.  offset is bounded by the call site (offset<16)
 * and the write distance dstEnd-dstPtr is bounded to [9,16] so every offset case
 * (1/2/4 inline, else -> base) writes exactly 16 bytes and stays fully unrolled. */
__CPROVER_requires(offset >= 1 && offset <= 15)
__CPROVER_requires(__CPROVER_is_fresh(srcPtr, 48))
__CPROVER_requires(srcPtr + offset == dstPtr)
__CPROVER_requires(__CPROVER_same_object(dstPtr, dstEnd))
__CPROVER_requires(__CPROVER_POINTER_OFFSET(dstEnd) - __CPROVER_POINTER_OFFSET(dstPtr) >= 9)
__CPROVER_requires(__CPROVER_POINTER_OFFSET(dstEnd) - __CPROVER_POINTER_OFFSET(dstPtr) <= 16)
__CPROVER_assigns(__CPROVER_object_upto(dstPtr, 16))
__CPROVER_ensures(__CPROVER_forall {
    size_t avocado_spec_i;
    (avocado_spec_i < 16) ==>
        (dstPtr[avocado_spec_i] == srcPtr[avocado_spec_i % offset])
})
{
    BYTE v[8];

    assert(dstEnd >= dstPtr + MINMATCH);

    switch(offset) {
    case 1:
        MEM_INIT(v, *srcPtr, 8);
        break;
    case 2:
        LZ4_memcpy(v, srcPtr, 2);
        LZ4_memcpy(&v[2], srcPtr, 2);
#if defined(_MSC_VER) && (_MSC_VER <= 1937) /* MSVC 2022 ver 17.7 or earlier */
#  pragma warning(push)
#  pragma warning(disable : 6385) /* warning C6385: Reading invalid data from 'v'. */
#endif
        LZ4_memcpy(&v[4], v, 4);
#if defined(_MSC_VER) && (_MSC_VER <= 1937) /* MSVC 2022 ver 17.7 or earlier */
#  pragma warning(pop)
#endif
        break;
    case 4:
        LZ4_memcpy(v, srcPtr, 4);
        LZ4_memcpy(&v[4], srcPtr, 4);
        break;
    default:
        LZ4_memcpy_using_offset_base(dstPtr, srcPtr, dstEnd, offset);
        return;
    }

    LZ4_memcpy(dstPtr, v, 8);
    dstPtr += 8;
    while (dstPtr < dstEnd) {
        LZ4_memcpy(dstPtr, v, 8);
        dstPtr += 8;
    }
}
#endif


/*-************************************
*  Common functions
**************************************/
static unsigned LZ4_NbCommonBytes (reg_t val)
/* LZ4_isLittleEndian() is replaced by its contract (nondeterministic 0/1), so
   both the little-endian (trailing-zero-byte count) and big-endian
   (leading-zero-byte count) paths are reachable; reg_t is 8 bytes here. */
__CPROVER_requires(val != 0)
__CPROVER_ensures(
    __CPROVER_return_value <= 7 && (
        /* little endian: number of trailing zero bytes = ctz(val)/8 */
        (    (val & ((((reg_t)1) << (8 * __CPROVER_return_value)) - 1)) == 0
          && ((val >> (8 * __CPROVER_return_value)) & 0xFF) != 0 )
     ||
        /* big endian: number of leading zero bytes = clz(val)/8 */
        (    (val >> (8 * (7 - __CPROVER_return_value))) != 0
          && ( __CPROVER_return_value == 0
               || (val >> (8 * (8 - __CPROVER_return_value))) == 0 ) )
    )
)
{
    assert(val != 0);
    if (LZ4_isLittleEndian()) {
        if (sizeof(val) == 8) {
#       if defined(_MSC_VER) && (_MSC_VER >= 1800) && (defined(_M_AMD64) && !defined(_M_ARM64EC)) && !defined(LZ4_FORCE_SW_BITCOUNT)
/*-*************************************************************************************************
* ARM64EC is a Microsoft-designed ARM64 ABI compatible with AMD64 applications on ARM64 Windows 11.
* The ARM64EC ABI does not support AVX/AVX2/AVX512 instructions, nor their relevant intrinsics
* including _tzcnt_u64. Therefore, we need to neuter the _tzcnt_u64 code path for ARM64EC.
****************************************************************************************************/
#         if defined(__clang__) && (__clang_major__ < 10)
            /* Avoid undefined clang-cl intrinsics issue.
             * See https://github.com/lz4/lz4/pull/1017 for details. */
            return (unsigned)__builtin_ia32_tzcnt_u64(val) >> 3;
#         else
            /* x64 CPUS without BMI support interpret `TZCNT` as `REP BSF` */
            return (unsigned)_tzcnt_u64(val) >> 3;
#         endif
#       elif defined(_MSC_VER) && defined(_WIN64) && !defined(LZ4_FORCE_SW_BITCOUNT)
            unsigned long r = 0;
            _BitScanForward64(&r, (U64)val);
            return (unsigned)r >> 3;
#       elif (defined(__clang__) || (defined(__GNUC__) && ((__GNUC__ > 3) || \
                            ((__GNUC__ == 3) && (__GNUC_MINOR__ >= 4))))) && \
                                        !defined(LZ4_FORCE_SW_BITCOUNT)
            return (unsigned)__builtin_ctzll((U64)val) >> 3;
#       else
            const U64 m = 0x0101010101010101ULL;
            val ^= val - 1;
            return (unsigned)(((U64)((val & (m - 1)) * m)) >> 56);
#       endif
        } else /* 32 bits */ {
#       if defined(_MSC_VER) && (_MSC_VER >= 1400) && !defined(LZ4_FORCE_SW_BITCOUNT)
            unsigned long r;
            _BitScanForward(&r, (U32)val);
            return (unsigned)r >> 3;
#       elif (defined(__clang__) || (defined(__GNUC__) && ((__GNUC__ > 3) || \
                            ((__GNUC__ == 3) && (__GNUC_MINOR__ >= 4))))) && \
                        !defined(__TINYC__) && !defined(LZ4_FORCE_SW_BITCOUNT)
            return (unsigned)__builtin_ctz((U32)val) >> 3;
#       else
            const U32 m = 0x01010101;
            return (unsigned)((((val - 1) ^ val) & (m - 1)) * m) >> 24;
#       endif
        }
    } else   /* Big Endian CPU */ {
        if (sizeof(val)==8) {
#       if (defined(__clang__) || (defined(__GNUC__) && ((__GNUC__ > 3) || \
                            ((__GNUC__ == 3) && (__GNUC_MINOR__ >= 4))))) && \
                        !defined(__TINYC__) && !defined(LZ4_FORCE_SW_BITCOUNT)
            return (unsigned)__builtin_clzll((U64)val) >> 3;
#       else
#if 1
            /* this method is probably faster,
             * but adds a 128 bytes lookup table */
            static const unsigned char ctz7_tab[128] = {
                7, 0, 1, 0, 2, 0, 1, 0, 3, 0, 1, 0, 2, 0, 1, 0,
                4, 0, 1, 0, 2, 0, 1, 0, 3, 0, 1, 0, 2, 0, 1, 0,
                5, 0, 1, 0, 2, 0, 1, 0, 3, 0, 1, 0, 2, 0, 1, 0,
                4, 0, 1, 0, 2, 0, 1, 0, 3, 0, 1, 0, 2, 0, 1, 0,
                6, 0, 1, 0, 2, 0, 1, 0, 3, 0, 1, 0, 2, 0, 1, 0,
                4, 0, 1, 0, 2, 0, 1, 0, 3, 0, 1, 0, 2, 0, 1, 0,
                5, 0, 1, 0, 2, 0, 1, 0, 3, 0, 1, 0, 2, 0, 1, 0,
                4, 0, 1, 0, 2, 0, 1, 0, 3, 0, 1, 0, 2, 0, 1, 0,
            };
            U64 const mask = 0x0101010101010101ULL;
            U64 const t = (((val >> 8) - mask) | val) & mask;
            return ctz7_tab[(t * 0x0080402010080402ULL) >> 57];
#else
            /* this method doesn't consume memory space like the previous one,
             * but it contains several branches,
             * that may end up slowing execution */
            static const U32 by32 = sizeof(val)*4;  /* 32 on 64 bits (goal), 16 on 32 bits.
            Just to avoid some static analyzer complaining about shift by 32 on 32-bits target.
            Note that this code path is never triggered in 32-bits mode. */
            unsigned r;
            if (!(val>>by32)) { r=4; } else { r=0; val>>=by32; }
            if (!(val>>16)) { r+=2; val>>=8; } else { val>>=24; }
            r += (!val);
            return r;
#endif
#       endif
        } else /* 32 bits */ {
#       if (defined(__clang__) || (defined(__GNUC__) && ((__GNUC__ > 3) || \
                            ((__GNUC__ == 3) && (__GNUC_MINOR__ >= 4))))) && \
                                        !defined(LZ4_FORCE_SW_BITCOUNT)
            return (unsigned)__builtin_clz((U32)val) >> 3;
#       else
            val >>= 8;
            val = ((((val + 0x00FFFF00) | 0x00FFFFFF) + val) |
              (val + 0x00FF0000)) >> 24;
            return (unsigned)val ^ 3;
#       endif
        }
    }
}


#define STEPSIZE sizeof(reg_t)
LZ4_FORCE_INLINE
unsigned LZ4_count(const BYTE* pIn, const BYTE* pMatch, const BYTE* pInLimit)
__CPROVER_requires(__CPROVER_is_fresh(pIn, 16))
__CPROVER_requires(__CPROVER_r_ok(pMatch, 16))
__CPROVER_requires(__CPROVER_same_object(pIn, pInLimit))
__CPROVER_requires(pIn <= pInLimit && pInLimit <= pIn + 16)
__CPROVER_assigns()
__CPROVER_ensures(1 == 2)
{
    const BYTE* const pStart = pIn;

    if (likely(pIn < pInLimit-(STEPSIZE-1))) {
        reg_t const diff = LZ4_read_ARCH(pMatch) ^ LZ4_read_ARCH(pIn);
        if (!diff) {
            pIn+=STEPSIZE; pMatch+=STEPSIZE;
        } else {
            return LZ4_NbCommonBytes(diff);
    }   }

    while (likely(pIn < pInLimit-(STEPSIZE-1))) {
        reg_t const diff = LZ4_read_ARCH(pMatch) ^ LZ4_read_ARCH(pIn);
        if (!diff) { pIn+=STEPSIZE; pMatch+=STEPSIZE; continue; }
        pIn += LZ4_NbCommonBytes(diff);
        return (unsigned)(pIn - pStart);
    }

    if ((STEPSIZE==8) && (pIn<(pInLimit-3)) && (LZ4_read32(pMatch) == LZ4_read32(pIn))) { pIn+=4; pMatch+=4; }
    if ((pIn<(pInLimit-1)) && (LZ4_read16(pMatch) == LZ4_read16(pIn))) { pIn+=2; pMatch+=2; }
    if ((pIn<pInLimit) && (*pMatch == *pIn)) pIn++;
    return (unsigned)(pIn - pStart);
}


#ifndef LZ4_COMMONDEFS_ONLY
/*-************************************
*  Local Constants
**************************************/
static const int LZ4_64Klimit = ((64 KB) + (MFLIMIT-1));
static const U32 LZ4_skipTrigger = 6;  /* Increase this value ==> compression run slower on incompressible data */


/*-************************************
*  Local Structures and types
**************************************/
typedef enum { clearedTable = 0, byPtr, byU32, byU16 } tableType_t;

/**
 * This enum distinguishes several different modes of accessing previous
 * content in the stream.
 *
 * - noDict        : There is no preceding content.
 * - withPrefix64k : Table entries up to ctx->dictSize before the current blob
 *                   blob being compressed are valid and refer to the preceding
 *                   content (of length ctx->dictSize), which is available
 *                   contiguously preceding in memory the content currently
 *                   being compressed.
 * - usingExtDict  : Like withPrefix64k, but the preceding content is somewhere
 *                   else in memory, starting at ctx->dictionary with length
 *                   ctx->dictSize.
 * - usingDictCtx  : Everything concerning the preceding content is
 *                   in a separate context, pointed to by ctx->dictCtx.
 *                   ctx->dictionary, ctx->dictSize, and table entries
 *                   in the current context that refer to positions
 *                   preceding the beginning of the current compression are
 *                   ignored. Instead, ctx->dictCtx->dictionary and ctx->dictCtx
 *                   ->dictSize describe the location and size of the preceding
 *                   content, and matches are found by looking in the ctx
 *                   ->dictCtx->hashTable.
 */
typedef enum { noDict = 0, withPrefix64k, usingExtDict, usingDictCtx } dict_directive;
typedef enum { noDictIssue = 0, dictSmall } dictIssue_directive;


/*-************************************
*  Local Utils
**************************************/
int LZ4_versionNumber (void)
__CPROVER_ensures(__CPROVER_return_value == LZ4_VERSION_NUMBER)
{ return LZ4_VERSION_NUMBER; }
const char* LZ4_versionString(void)
__CPROVER_assigns()
__CPROVER_ensures(__CPROVER_return_value != 0)
__CPROVER_ensures(__CPROVER_r_ok(__CPROVER_return_value, sizeof(LZ4_VERSION_STRING)))
__CPROVER_ensures(
    __CPROVER_return_value[0] == LZ4_VERSION_STRING[0] &&
    __CPROVER_return_value[1] == LZ4_VERSION_STRING[1] &&
    __CPROVER_return_value[2] == LZ4_VERSION_STRING[2] &&
    __CPROVER_return_value[3] == LZ4_VERSION_STRING[3] &&
    __CPROVER_return_value[4] == LZ4_VERSION_STRING[4] &&
    __CPROVER_return_value[5] == LZ4_VERSION_STRING[5] &&
    __CPROVER_return_value[6] == LZ4_VERSION_STRING[6])
{ return LZ4_VERSION_STRING; }
int LZ4_compressBound(int isize)
__CPROVER_assigns()
__CPROVER_ensures(
    ((unsigned)isize > (unsigned)LZ4_MAX_INPUT_SIZE) ==>
        (__CPROVER_return_value == 0))
__CPROVER_ensures(
    ((unsigned)isize <= (unsigned)LZ4_MAX_INPUT_SIZE) ==>
        (__CPROVER_return_value == isize + (isize/255) + 16))
{ return LZ4_COMPRESSBOUND(isize); }
int LZ4_sizeofState(void)
__CPROVER_assigns()
__CPROVER_ensures(__CPROVER_return_value == (int)sizeof(LZ4_stream_t))
{ return sizeof(LZ4_stream_t); }


/*-****************************************
*  Internal Definitions, used only in Tests
*******************************************/
#if defined (__cplusplus)
extern "C" {
#endif

int LZ4_compress_forceExtDict (LZ4_stream_t* LZ4_dict, const char* source, char* dest, int srcSize);

int LZ4_decompress_safe_forceExtDict(const char* source, char* dest,
                                     int compressedSize, int maxOutputSize,
                                     const void* dictStart, size_t dictSize);
int LZ4_decompress_safe_partial_forceExtDict(const char* source, char* dest,
                                     int compressedSize, int targetOutputSize, int dstCapacity,
                                     const void* dictStart, size_t dictSize);
#if defined (__cplusplus)
}
#endif

/*-******************************
*  Compression functions
********************************/
LZ4_FORCE_INLINE U32 LZ4_hash4(U32 sequence, tableType_t const tableType)
__CPROVER_assigns()
__CPROVER_ensures(tableType == byU16 ==>
    __CPROVER_return_value < (1U << (LZ4_HASHLOG+1)))
__CPROVER_ensures(tableType == byU16 ==>
    (__CPROVER_return_value & 1u) ==
        (((sequence * 2654435761U) >> ((MINMATCH*8)-(LZ4_HASHLOG+1))) & 1u))
__CPROVER_ensures(tableType != byU16 ==>
    __CPROVER_return_value < (1U << LZ4_HASHLOG))
{
    if (tableType == byU16)
        return ((sequence * 2654435761U) >> ((MINMATCH*8)-(LZ4_HASHLOG+1)));
    else
        return ((sequence * 2654435761U) >> ((MINMATCH*8)-LZ4_HASHLOG));
}

LZ4_FORCE_INLINE U32 LZ4_hash5(U64 sequence, tableType_t const tableType)
__CPROVER_assigns()
__CPROVER_ensures(tableType == byU16 ==>
    __CPROVER_return_value < (1U << (LZ4_HASHLOG+1)))
__CPROVER_ensures(tableType != byU16 ==>
    __CPROVER_return_value < (1U << LZ4_HASHLOG))
{
    const U32 hashLog = (tableType == byU16) ? LZ4_HASHLOG+1 : LZ4_HASHLOG;
    if (LZ4_isLittleEndian()) {
        const U64 prime5bytes = 889523592379ULL;
        return (U32)(((sequence << 24) * prime5bytes) >> (64 - hashLog));
    } else {
        const U64 prime8bytes = 11400714785074694791ULL;
        return (U32)(((sequence >> 24) * prime8bytes) >> (64 - hashLog));
    }
}

LZ4_FORCE_INLINE U32 LZ4_hashPosition(const void* const p, tableType_t const tableType)
__CPROVER_requires(tableType == byPtr || tableType == byU32 || tableType == byU16)
__CPROVER_requires(__CPROVER_r_ok(p, sizeof(reg_t)))
__CPROVER_assigns()
__CPROVER_ensures(tableType == byU16 ==>
    __CPROVER_return_value < (1U << (LZ4_HASHLOG+1)))
__CPROVER_ensures(tableType != byU16 ==>
    __CPROVER_return_value < (1U << LZ4_HASHLOG))
__CPROVER_ensures(tableType == byU16 ==>
    (__CPROVER_return_value & 1u) ==
        (((((const LZ4_unalign32*)p)->u32 * 2654435761U) >> ((MINMATCH*8)-(LZ4_HASHLOG+1))) & 1u))
{
    if ((sizeof(reg_t)==8) && (tableType != byU16)) return LZ4_hash5(LZ4_read_ARCH(p), tableType);

#ifdef LZ4_STATIC_LINKING_ONLY_ENDIANNESS_INDEPENDENT_OUTPUT
    return LZ4_hash4(LZ4_readLE32(p), tableType);
#else
    return LZ4_hash4(LZ4_read32(p), tableType);
#endif
}

#define AVOCADO_SPEC_CLEARHASH_N 16

LZ4_FORCE_INLINE void LZ4_clearHash(U32 h, void* tableBase, tableType_t const tableType)
__CPROVER_requires(tableType == byPtr || tableType == byU32 || tableType == byU16)
__CPROVER_requires(h < AVOCADO_SPEC_CLEARHASH_N)
__CPROVER_requires(__CPROVER_is_fresh(tableBase, AVOCADO_SPEC_CLEARHASH_N * sizeof(const BYTE*)))
__CPROVER_assigns(
    tableType == byPtr: ((const BYTE**)tableBase)[h];
    tableType == byU32: ((U32*)tableBase)[h];
    tableType == byU16: ((U16*)tableBase)[h])
__CPROVER_ensures(tableType == byPtr ==> ((const BYTE**)tableBase)[h] == NULL)
__CPROVER_ensures(tableType == byU32 ==> ((U32*)tableBase)[h] == 0)
__CPROVER_ensures(tableType == byU16 ==> ((U16*)tableBase)[h] == 0)
{
    switch (tableType)
    {
    default: /* fallthrough */
    case clearedTable: { /* illegal! */ assert(0); return; }
    case byPtr: { const BYTE** hashTable = (const BYTE**)tableBase; hashTable[h] = NULL; return; }
    case byU32: { U32* hashTable = (U32*) tableBase; hashTable[h] = 0; return; }
    case byU16: { U16* hashTable = (U16*) tableBase; hashTable[h] = 0; return; }
    }
}

#define AVOCADO_SPEC_PUTINDEX_N 16

LZ4_FORCE_INLINE void LZ4_putIndexOnHash(U32 idx, U32 h, void* tableBase, tableType_t const tableType)
__CPROVER_requires(tableType == byU32 || tableType == byU16)
__CPROVER_requires(h < AVOCADO_SPEC_PUTINDEX_N)
__CPROVER_requires(tableType == byU16 ==> idx < 65536)
__CPROVER_requires(__CPROVER_is_fresh(tableBase, AVOCADO_SPEC_PUTINDEX_N * sizeof(U32)))
__CPROVER_assigns(
    tableType == byU32: ((U32*)tableBase)[h];
    tableType == byU16: ((U16*)tableBase)[h])
__CPROVER_ensures(tableType == byU32 ==> ((U32*)tableBase)[h] == idx)
__CPROVER_ensures(tableType == byU16 ==> ((U16*)tableBase)[h] == (U16)idx)
{
    switch (tableType)
    {
    default: /* fallthrough */
    case clearedTable: /* fallthrough */
    case byPtr: { /* illegal! */ assert(0); return; }
    case byU32: { U32* hashTable = (U32*) tableBase; hashTable[h] = idx; return; }
    case byU16: { U16* hashTable = (U16*) tableBase; assert(idx < 65536); hashTable[h] = (U16)idx; return; }
    }
}

/* LZ4_putPosition*() : only used in byPtr mode */
#define AVOCADO_SPEC_PUTPOS_N 16

/* LZ4_putPosition*() : only used in byPtr mode */
LZ4_FORCE_INLINE void LZ4_putPositionOnHash(const BYTE* p, U32 h,
                                  void* tableBase, tableType_t const tableType)
__CPROVER_requires(tableType == byPtr)
__CPROVER_requires(h < AVOCADO_SPEC_PUTPOS_N)
__CPROVER_requires(__CPROVER_is_fresh(tableBase, AVOCADO_SPEC_PUTPOS_N * sizeof(const BYTE*)))
__CPROVER_assigns(((const BYTE**)tableBase)[h])
__CPROVER_ensures(((const BYTE**)tableBase)[h] == p)
{
    const BYTE** const hashTable = (const BYTE**)tableBase;
    assert(tableType == byPtr); (void)tableType;
    hashTable[h] = p;
}

LZ4_FORCE_INLINE void LZ4_putPosition(const BYTE* p, void* tableBase, tableType_t tableType)
__CPROVER_requires(tableType == byPtr)
__CPROVER_requires(__CPROVER_r_ok(p, sizeof(reg_t)))
__CPROVER_requires(__CPROVER_is_fresh(tableBase, AVOCADO_SPEC_PUTPOS_N * sizeof(const BYTE*)))
__CPROVER_assigns(__CPROVER_object_whole((const BYTE**)tableBase))
__CPROVER_ensures(__CPROVER_exists {
    U32 avocado_spec_h;
    (avocado_spec_h < AVOCADO_SPEC_PUTPOS_N) &&
        (((const BYTE**)tableBase)[avocado_spec_h] == p) })
{
    U32 const h = LZ4_hashPosition(p, tableType);
    LZ4_putPositionOnHash(p, h, tableBase, tableType);
}

/* LZ4_getIndexOnHash() :
 * Index of match position registered in hash table.
 * hash position must be calculated by using base+index, or dictBase+index.
 * Assumption 1 : only valid if tableType == byU32 or byU16.
 * Assumption 2 : h is presumed valid (within limits of hash table)
 */
#define AVOCADO_SPEC_GETINDEX_N 16

/* LZ4_getIndexOnHash() :
 * Index of match position registered in hash table.
 * hash position must be calculated by using base+index, or dictBase+index.
 * Assumption 1 : only valid if tableType == byU32 or byU16.
 * Assumption 2 : h is presumed valid (within limits of hash table)
 */
LZ4_FORCE_INLINE U32 LZ4_getIndexOnHash(U32 h, const void* tableBase, tableType_t tableType)
__CPROVER_requires(tableType == byU32 || tableType == byU16)
__CPROVER_requires(h < AVOCADO_SPEC_GETINDEX_N)
__CPROVER_requires(__CPROVER_is_fresh(tableBase, AVOCADO_SPEC_GETINDEX_N * sizeof(U32)))
__CPROVER_assigns()
__CPROVER_ensures(tableType == byU32 ==> __CPROVER_return_value == ((const U32*)tableBase)[h])
__CPROVER_ensures(tableType == byU16 ==> __CPROVER_return_value == ((const U16*)tableBase)[h])
{
    LZ4_STATIC_ASSERT(LZ4_MEMORY_USAGE > 2);
    if (tableType == byU32) {
        const U32* const hashTable = (const U32*) tableBase;
        assert(h < (1U << (LZ4_MEMORY_USAGE-2)));
        return hashTable[h];
    }
    if (tableType == byU16) {
        const U16* const hashTable = (const U16*) tableBase;
        assert(h < (1U << (LZ4_MEMORY_USAGE-1)));
        return hashTable[h];
    }
    assert(0); return 0;  /* forbidden case */
}

#define AVOCADO_SPEC_GETPOS_N 16

/* Full byPtr hash-table size, so LZ4_hashPosition's bound (h < 1<<LZ4_HASHLOG)
 * satisfies this precondition at LZ4_getPosition's call site. */
#define AVOCADO_SPEC_GETPOS_FULL_N (1U << LZ4_HASHLOG)

static const BYTE* LZ4_getPositionOnHash(U32 h, const void* tableBase, tableType_t tableType)
__CPROVER_requires(tableType == byPtr)
__CPROVER_requires(h < AVOCADO_SPEC_GETPOS_FULL_N)
__CPROVER_requires(__CPROVER_is_fresh(tableBase, AVOCADO_SPEC_GETPOS_FULL_N * sizeof(const BYTE*)))
__CPROVER_assigns()
__CPROVER_ensures(__CPROVER_return_value == ((const BYTE* const*)tableBase)[h])
{
    assert(tableType == byPtr); (void)tableType;
    { const BYTE* const* hashTable = (const BYTE* const*) tableBase; return hashTable[h]; }
}

LZ4_FORCE_INLINE const BYTE*
LZ4_getPosition(const BYTE* p,
                const void* tableBase, tableType_t tableType)
__CPROVER_requires(tableType == byPtr)
__CPROVER_requires(__CPROVER_r_ok(p, sizeof(reg_t)))
__CPROVER_requires(__CPROVER_is_fresh(tableBase, AVOCADO_SPEC_GETPOS_FULL_N * sizeof(const BYTE*)))
__CPROVER_assigns()
{
    U32 const h = LZ4_hashPosition(p, tableType);
    return LZ4_getPositionOnHash(h, tableBase, tableType);
}

#define AVOCADO_SPEC_PREPARETABLE_RESET \
    ( (tableType_t)__CPROVER_old(cctx->tableType) != clearedTable \
      && ( (tableType_t)__CPROVER_old(cctx->tableType) != tableType \
           || (tableType == byU16 && __CPROVER_old(cctx->currentOffset) + (unsigned)inputSize >= 0xFFFFU) \
           || (tableType == byU32 && __CPROVER_old(cctx->currentOffset) > 1 GB) \
           || tableType == byPtr \
           || inputSize >= 4 KB ) )

LZ4_FORCE_INLINE void
LZ4_prepareTable(LZ4_stream_t_internal* const cctx,
           const int inputSize,
           const tableType_t tableType)
__CPROVER_requires(__CPROVER_is_fresh(cctx, sizeof(*cctx)))
__CPROVER_requires(inputSize >= 0)
__CPROVER_requires(tableType == clearedTable || tableType == byPtr
                   || tableType == byU32 || tableType == byU16)
__CPROVER_assigns(__CPROVER_object_whole(cctx->hashTable),
                  cctx->currentOffset, cctx->tableType,
                  cctx->dictCtx, cctx->dictionary, cctx->dictSize)
__CPROVER_ensures(cctx->dictCtx == NULL)
__CPROVER_ensures(cctx->dictionary == NULL)
__CPROVER_ensures(cctx->dictSize == 0)
__CPROVER_ensures(AVOCADO_SPEC_PREPARETABLE_RESET ==> cctx->tableType == (U32)clearedTable)
__CPROVER_ensures(!AVOCADO_SPEC_PREPARETABLE_RESET ==> cctx->tableType == __CPROVER_old(cctx->tableType))
__CPROVER_ensures(AVOCADO_SPEC_PREPARETABLE_RESET ==> cctx->currentOffset == 0)
__CPROVER_ensures(!AVOCADO_SPEC_PREPARETABLE_RESET ==> cctx->currentOffset ==
    ( (__CPROVER_old(cctx->currentOffset) != 0 && tableType == byU32)
      ? (U32)(__CPROVER_old(cctx->currentOffset) + 64 KB)
      : __CPROVER_old(cctx->currentOffset) ))
__CPROVER_ensures(AVOCADO_SPEC_PREPARETABLE_RESET ==>
    (cctx->hashTable[0] == 0 && cctx->hashTable[1] == 0
     && cctx->hashTable[2] == 0 && cctx->hashTable[3] == 0))
{
    /* If the table hasn't been used, it's guaranteed to be zeroed out, and is
     * therefore safe to use no matter what mode we're in. Otherwise, we figure
     * out if it's safe to leave as is or whether it needs to be reset.
     */
    if ((tableType_t)cctx->tableType != clearedTable) {
        assert(inputSize >= 0);
        if ((tableType_t)cctx->tableType != tableType
          || ((tableType == byU16) && cctx->currentOffset + (unsigned)inputSize >= 0xFFFFU)
          || ((tableType == byU32) && cctx->currentOffset > 1 GB)
          || tableType == byPtr
          || inputSize >= 4 KB)
        {
            DEBUGLOG(4, "LZ4_prepareTable: Resetting table in %p", cctx);
            MEM_INIT(cctx->hashTable, 0, LZ4_HASHTABLESIZE);
            cctx->currentOffset = 0;
            cctx->tableType = (U32)clearedTable;
        } else {
            DEBUGLOG(4, "LZ4_prepareTable: Re-use hash table (no reset)");
        }
    }

    /* Adding a gap, so all previous entries are > LZ4_DISTANCE_MAX back,
     * is faster than compressing without a gap.
     * However, compressing with currentOffset == 0 is faster still,
     * so we preserve that case.
     */
    if (cctx->currentOffset != 0 && tableType == byU32) {
        DEBUGLOG(5, "LZ4_prepareTable: adding 64KB to currentOffset");
        cctx->currentOffset += 64 KB;
    }

    /* Finally, clear history */
    cctx->dictCtx = NULL;
    cctx->dictionary = NULL;
    cctx->dictSize = 0;
}

/** LZ4_compress_generic_validated() :
 *  inlined, to ensure branches are decided at compilation time.
 *  The following conditions are presumed already validated:
 *  - source != NULL
 *  - inputSize > 0
 */
#define AVOCADO_SPEC_CGV_MAXOUT 20
#define AVOCADO_SPEC_CGV_DSTCAP 64

/** LZ4_compress_generic_validated() :
 *  inlined, to ensure branches are decided at compilation time.
 *  The following conditions are presumed already validated:
 *  - source != NULL
 *  - inputSize > 0
 */
LZ4_FORCE_INLINE int LZ4_compress_generic_validated(
                 LZ4_stream_t_internal* const cctx,
                 const char* const source,
                 char* const dest,
                 const int inputSize,
                 int*  inputConsumed, /* only written when outputDirective == fillOutput */
                 const int maxOutputSize,
                 const limitedOutput_directive outputDirective,
                 const tableType_t tableType,
                 const dict_directive dictDirective,
                 const dictIssue_directive dictIssue,
                 const int acceleration)
/* This contract covers the "all literals" fast path, reached when
 * inputSize < LZ4_minLength (==13): the entire main matching loop and
 * every hash-table callee are skipped, so control goes straight to
 * _last_literals.  inputSize is pinned to {11,12} so that the
 * unconditionally-computed mflimitPlusOne/matchlimit pointers stay inside
 * the source object.  dictDirective==noDict with a NULL dictionary and a
 * zero currentOffset keeps all pointer arithmetic in bounds. */
__CPROVER_requires(inputSize >= 11 && inputSize <= 12)
__CPROVER_requires(tableType == byPtr || tableType == byU32 || tableType == byU16)
__CPROVER_requires(dictDirective == noDict)
__CPROVER_requires(acceleration >= 1)
__CPROVER_requires(outputDirective == notLimited
                || outputDirective == limitedOutput
                || outputDirective == fillOutput)
__CPROVER_requires(maxOutputSize >= 0 && maxOutputSize <= AVOCADO_SPEC_CGV_MAXOUT)
__CPROVER_requires(__CPROVER_is_fresh(cctx, sizeof(LZ4_stream_t_internal)))
__CPROVER_requires(cctx->currentOffset == 0)
__CPROVER_requires(cctx->dictionary == NULL)
__CPROVER_requires(__CPROVER_is_fresh(source, (size_t)inputSize))
__CPROVER_requires(__CPROVER_is_fresh(dest, AVOCADO_SPEC_CGV_DSTCAP))
__CPROVER_requires(outputDirective == fillOutput
                ==> __CPROVER_is_fresh(inputConsumed, sizeof(int)))
__CPROVER_assigns(cctx->dictSize, cctx->currentOffset, cctx->tableType)
__CPROVER_assigns(__CPROVER_object_upto(dest, (size_t)inputSize + 1))
__CPROVER_assigns(outputDirective == fillOutput:
                  __CPROVER_object_upto(inputConsumed, sizeof(int)))
/* Return value, by output directive */
__CPROVER_ensures(
    (outputDirective == fillOutput && maxOutputSize == 0)
        ==> __CPROVER_return_value == 0)
__CPROVER_ensures(
    (outputDirective == limitedOutput && maxOutputSize < inputSize + 1)
        ==> __CPROVER_return_value == 0)
__CPROVER_ensures(
    (outputDirective == notLimited
     || (outputDirective == limitedOutput && maxOutputSize >= inputSize + 1)
     || (outputDirective == fillOutput && maxOutputSize >= inputSize + 1))
        ==> __CPROVER_return_value == inputSize + 1)
__CPROVER_ensures(
    (outputDirective == fillOutput && maxOutputSize >= 1
      && maxOutputSize < inputSize + 1)
        ==> __CPROVER_return_value == maxOutputSize)
/* Context state updates (skipped only on the early fillOutput return) */
__CPROVER_ensures(
    !(outputDirective == fillOutput && maxOutputSize == 0)
        ==> cctx->tableType == (U32)tableType)
__CPROVER_ensures(
    !(outputDirective == fillOutput && maxOutputSize == 0)
        ==> cctx->currentOffset == (U32)inputSize)
__CPROVER_ensures(
    !(outputDirective == fillOutput && maxOutputSize == 0)
        ==> cctx->dictSize == __CPROVER_old(cctx->dictSize) + (U32)inputSize)
__CPROVER_ensures(
    (outputDirective == fillOutput && maxOutputSize == 0)
        ==> cctx->currentOffset == 0)
/* Output bytes on the full-copy paths: token then verbatim literals */
__CPROVER_ensures(
    (outputDirective == notLimited
     || (outputDirective == limitedOutput && maxOutputSize >= inputSize + 1)
     || (outputDirective == fillOutput && maxOutputSize >= inputSize + 1))
        ==> ((BYTE*)dest)[0] == (BYTE)((unsigned)inputSize << ML_BITS))
__CPROVER_ensures(
    (outputDirective == notLimited
     || (outputDirective == limitedOutput && maxOutputSize >= inputSize + 1)
     || (outputDirective == fillOutput && maxOutputSize >= inputSize + 1))
        ==> __CPROVER_forall {
                int i;
                (i >= 0 && i < inputSize)
                    ==> ((BYTE*)dest)[i + 1] == ((const BYTE*)source)[i] })
/* Output bytes on the fillOutput truncated path */
__CPROVER_ensures(
    (outputDirective == fillOutput && maxOutputSize >= 1
      && maxOutputSize < inputSize + 1)
        ==> ((BYTE*)dest)[0] == (BYTE)((unsigned)(maxOutputSize - 1) << ML_BITS))
__CPROVER_ensures(
    (outputDirective == fillOutput && maxOutputSize >= 1
      && maxOutputSize < inputSize + 1)
        ==> __CPROVER_forall {
                int j;
                (j >= 0 && j < maxOutputSize - 1)
                    ==> ((BYTE*)dest)[j + 1] == ((const BYTE*)source)[j] })
/* inputConsumed, written only under fillOutput */
__CPROVER_ensures(
    (outputDirective == fillOutput && maxOutputSize >= inputSize + 1)
        ==> *inputConsumed == inputSize)
__CPROVER_ensures(
    (outputDirective == fillOutput && maxOutputSize >= 1
      && maxOutputSize < inputSize + 1)
        ==> *inputConsumed == maxOutputSize - 1)
{
    int result;
    const BYTE* ip = (const BYTE*)source;

    U32 const startIndex = cctx->currentOffset;
    const BYTE* base = (const BYTE*)source - startIndex;
    const BYTE* lowLimit;

    const LZ4_stream_t_internal* dictCtx = (const LZ4_stream_t_internal*) cctx->dictCtx;
    const BYTE* const dictionary =
        dictDirective == usingDictCtx ? dictCtx->dictionary : cctx->dictionary;
    const U32 dictSize =
        dictDirective == usingDictCtx ? dictCtx->dictSize : cctx->dictSize;
    const U32 dictDelta =
        (dictDirective == usingDictCtx) ? startIndex - dictCtx->currentOffset : 0;   /* make indexes in dictCtx comparable with indexes in current context */

    int const maybe_extMem = (dictDirective == usingExtDict) || (dictDirective == usingDictCtx);
    U32 const prefixIdxLimit = startIndex - dictSize;   /* used when dictDirective == dictSmall */
    const BYTE* const dictEnd = dictionary ? dictionary + dictSize : dictionary;
    const BYTE* anchor = (const BYTE*) source;
    const BYTE* const iend = ip + inputSize;
    const BYTE* const mflimitPlusOne = iend - MFLIMIT + 1;
    const BYTE* const matchlimit = iend - LASTLITERALS;

    /* the dictCtx currentOffset is indexed on the start of the dictionary,
     * while a dictionary in the current context precedes the currentOffset */
    const BYTE* dictBase = (dictionary == NULL) ? NULL :
                           (dictDirective == usingDictCtx) ?
                            dictionary + dictSize - dictCtx->currentOffset :
                            dictionary + dictSize - startIndex;

    BYTE* op = (BYTE*) dest;
    BYTE* const olimit = op + maxOutputSize;

    U32 offset = 0;
    U32 forwardH;

    DEBUGLOG(5, "LZ4_compress_generic_validated: srcSize=%i, tableType=%u", inputSize, tableType);
    assert(ip != NULL);
    if (tableType == byU16) assert(inputSize<LZ4_64Klimit);  /* Size too large (not within 64K limit) */
    if (tableType == byPtr) assert(dictDirective==noDict);   /* only supported use case with byPtr */
    /* If init conditions are not met, we don't have to mark stream
     * as having dirty context, since no action was taken yet */
    if (outputDirective == fillOutput && maxOutputSize < 1) { return 0; } /* Impossible to store anything */
    assert(acceleration >= 1);

    lowLimit = (const BYTE*)source - (dictDirective == withPrefix64k ? dictSize : 0);

    /* Update context state */
    if (dictDirective == usingDictCtx) {
        /* Subsequent linked blocks can't use the dictionary. */
        /* Instead, they use the block we just compressed. */
        cctx->dictCtx = NULL;
        cctx->dictSize = (U32)inputSize;
    } else {
        cctx->dictSize += (U32)inputSize;
    }
    cctx->currentOffset += (U32)inputSize;
    cctx->tableType = (U32)tableType;

    if (inputSize<LZ4_minLength) goto _last_literals;        /* Input too small, no compression (all literals) */

    /* First Byte */
    {   U32 const h = LZ4_hashPosition(ip, tableType);
        if (tableType == byPtr) {
            LZ4_putPositionOnHash(ip, h, cctx->hashTable, byPtr);
        } else {
            LZ4_putIndexOnHash(startIndex, h, cctx->hashTable, tableType);
    }   }
    ip++; forwardH = LZ4_hashPosition(ip, tableType);

    /* Main Loop */
    for ( ; ; ) {
        const BYTE* match;
        BYTE* token;
        const BYTE* filledIp;

        /* Find a match */
        if (tableType == byPtr) {
            const BYTE* forwardIp = ip;
            int step = 1;
            int searchMatchNb = acceleration << LZ4_skipTrigger;
            do {
                U32 const h = forwardH;
                ip = forwardIp;
                forwardIp += step;
                step = (searchMatchNb++ >> LZ4_skipTrigger);

                if (unlikely(forwardIp > mflimitPlusOne)) goto _last_literals;
                assert(ip < mflimitPlusOne);

                match = LZ4_getPositionOnHash(h, cctx->hashTable, tableType);
                forwardH = LZ4_hashPosition(forwardIp, tableType);
                LZ4_putPositionOnHash(ip, h, cctx->hashTable, tableType);

            } while ( (match+LZ4_DISTANCE_MAX < ip)
                   || (LZ4_read32(match) != LZ4_read32(ip)) );

        } else {   /* byU32, byU16 */

            const BYTE* forwardIp = ip;
            int step = 1;
            int searchMatchNb = acceleration << LZ4_skipTrigger;
            do {
                U32 const h = forwardH;
                U32 const current = (U32)(forwardIp - base);
                U32 matchIndex = LZ4_getIndexOnHash(h, cctx->hashTable, tableType);
                assert(matchIndex <= current);
                assert(forwardIp - base < (ptrdiff_t)(2 GB - 1));
                ip = forwardIp;
                forwardIp += step;
                step = (searchMatchNb++ >> LZ4_skipTrigger);

                if (unlikely(forwardIp > mflimitPlusOne)) goto _last_literals;
                assert(ip < mflimitPlusOne);

                if (dictDirective == usingDictCtx) {
                    if (matchIndex < startIndex) {
                        /* there was no match, try the dictionary */
                        assert(tableType == byU32);
                        matchIndex = LZ4_getIndexOnHash(h, dictCtx->hashTable, byU32);
                        match = dictBase + matchIndex;
                        matchIndex += dictDelta;   /* make dictCtx index comparable with current context */
                        lowLimit = dictionary;
                    } else {
                        match = base + matchIndex;
                        lowLimit = (const BYTE*)source;
                    }
                } else if (dictDirective == usingExtDict) {
                    if (matchIndex < startIndex) {
                        DEBUGLOG(7, "extDict candidate: matchIndex=%5u  <  startIndex=%5u", matchIndex, startIndex);
                        assert(startIndex - matchIndex >= MINMATCH);
                        assert(dictBase);
                        match = dictBase + matchIndex;
                        lowLimit = dictionary;
                    } else {
                        match = base + matchIndex;
                        lowLimit = (const BYTE*)source;
                    }
                } else {   /* single continuous memory segment */
                    match = base + matchIndex;
                }
                forwardH = LZ4_hashPosition(forwardIp, tableType);
                LZ4_putIndexOnHash(current, h, cctx->hashTable, tableType);

                DEBUGLOG(7, "candidate at pos=%u  (offset=%u \n", matchIndex, current - matchIndex);
                if ((dictIssue == dictSmall) && (matchIndex < prefixIdxLimit)) { continue; }    /* match outside of valid area */
                assert(matchIndex < current);
                if ( ((tableType != byU16) || (LZ4_DISTANCE_MAX < LZ4_DISTANCE_ABSOLUTE_MAX))
                  && (matchIndex+LZ4_DISTANCE_MAX < current)) {
                    continue;
                } /* too far */
                assert((current - matchIndex) <= LZ4_DISTANCE_MAX);  /* match now expected within distance */

                if (LZ4_read32(match) == LZ4_read32(ip)) {
                    if (maybe_extMem) offset = current - matchIndex;
                    break;   /* match found */
                }

            } while(1);
        }

        /* Catch up */
        filledIp = ip;
        assert(ip > anchor); /* this is always true as ip has been advanced before entering the main loop */
        if ((match > lowLimit) && unlikely(ip[-1] == match[-1])) {
            do { ip--; match--; } while (((ip > anchor) & (match > lowLimit)) && (unlikely(ip[-1] == match[-1])));
        }

        /* Encode Literals */
        {   unsigned const litLength = (unsigned)(ip - anchor);
            token = op++;
            if ((outputDirective == limitedOutput) &&  /* Check output buffer overflow */
                (unlikely(op + litLength + (2 + 1 + LASTLITERALS) + (litLength/255) > olimit)) ) {
                return 0;   /* cannot compress within `dst` budget. Stored indexes in hash table are nonetheless fine */
            }
            if ((outputDirective == fillOutput) &&
                (unlikely(op + (litLength+240)/255 /* litlen */ + litLength /* literals */ + 2 /* offset */ + 1 /* token */ + MFLIMIT - MINMATCH /* min last literals so last match is <= end - MFLIMIT */ > olimit))) {
                op--;
                goto _last_literals;
            }
            if (litLength >= RUN_MASK) {
                unsigned len = litLength - RUN_MASK;
                *token = (RUN_MASK<<ML_BITS);
                for(; len >= 255 ; len-=255) *op++ = 255;
                *op++ = (BYTE)len;
            }
            else *token = (BYTE)(litLength<<ML_BITS);

            /* Copy Literals */
            LZ4_wildCopy8(op, anchor, op+litLength);
            op+=litLength;
            DEBUGLOG(6, "seq.start:%i, literals=%u, match.start:%i",
                        (int)(anchor-(const BYTE*)source), litLength, (int)(ip-(const BYTE*)source));
        }

_next_match:
        /* at this stage, the following variables must be correctly set :
         * - ip : at start of LZ operation
         * - match : at start of previous pattern occurrence; can be within current prefix, or within extDict
         * - offset : if maybe_ext_memSegment==1 (constant)
         * - lowLimit : must be == dictionary to mean "match is within extDict"; must be == source otherwise
         * - token and *token : position to write 4-bits for match length; higher 4-bits for literal length supposed already written
         */

        if ((outputDirective == fillOutput) &&
            (op + 2 /* offset */ + 1 /* token */ + MFLIMIT - MINMATCH /* min last literals so last match is <= end - MFLIMIT */ > olimit)) {
            /* the match was too close to the end, rewind and go to last literals */
            op = token;
            goto _last_literals;
        }

        /* Encode Offset */
        if (maybe_extMem) {   /* static test */
            DEBUGLOG(6, "             with offset=%u  (ext if > %i)", offset, (int)(ip - (const BYTE*)source));
            assert(offset <= LZ4_DISTANCE_MAX && offset > 0);
            LZ4_writeLE16(op, (U16)offset); op+=2;
        } else  {
            DEBUGLOG(6, "             with offset=%u  (same segment)", (U32)(ip - match));
            assert(ip-match <= LZ4_DISTANCE_MAX);
            LZ4_writeLE16(op, (U16)(ip - match)); op+=2;
        }

        /* Encode MatchLength */
        {   unsigned matchCode;

            if ( (dictDirective==usingExtDict || dictDirective==usingDictCtx)
              && (lowLimit==dictionary) /* match within extDict */ ) {
                const BYTE* limit = ip + (dictEnd-match);
                assert(dictEnd > match);
                if (limit > matchlimit) limit = matchlimit;
                matchCode = LZ4_count(ip+MINMATCH, match+MINMATCH, limit);
                ip += (size_t)matchCode + MINMATCH;
                if (ip==limit) {
                    unsigned const more = LZ4_count(limit, (const BYTE*)source, matchlimit);
                    matchCode += more;
                    ip += more;
                }
                DEBUGLOG(6, "             with matchLength=%u starting in extDict", matchCode+MINMATCH);
            } else {
                matchCode = LZ4_count(ip+MINMATCH, match+MINMATCH, matchlimit);
                ip += (size_t)matchCode + MINMATCH;
                DEBUGLOG(6, "             with matchLength=%u", matchCode+MINMATCH);
            }

            if ((outputDirective) &&    /* Check output buffer overflow */
                (unlikely(op + (1 + LASTLITERALS) + (matchCode+240)/255 > olimit)) ) {
                if (outputDirective == fillOutput) {
                    /* Match description too long : reduce it */
                    U32 newMatchCode = 15 /* in token */ - 1 /* to avoid needing a zero byte */ + ((U32)(olimit - op) - 1 - LASTLITERALS) * 255;
                    ip -= matchCode - newMatchCode;
                    assert(newMatchCode < matchCode);
                    matchCode = newMatchCode;
                    if (unlikely(ip <= filledIp)) {
                        /* We have already filled up to filledIp so if ip ends up less than filledIp
                         * we have positions in the hash table beyond the current position. This is
                         * a problem if we reuse the hash table. So we have to remove these positions
                         * from the hash table.
                         */
                        const BYTE* ptr;
                        DEBUGLOG(5, "Clearing %u positions", (U32)(filledIp - ip));
                        for (ptr = ip; ptr <= filledIp; ++ptr) {
                            U32 const h = LZ4_hashPosition(ptr, tableType);
                            LZ4_clearHash(h, cctx->hashTable, tableType);
                        }
                    }
                } else {
                    assert(outputDirective == limitedOutput);
                    return 0;   /* cannot compress within `dst` budget. Stored indexes in hash table are nonetheless fine */
                }
            }
            if (matchCode >= ML_MASK) {
                *token += ML_MASK;
                matchCode -= ML_MASK;
                LZ4_write32(op, 0xFFFFFFFF);
                while (matchCode >= 4*255) {
                    op+=4;
                    LZ4_write32(op, 0xFFFFFFFF);
                    matchCode -= 4*255;
                }
                op += matchCode / 255;
                *op++ = (BYTE)(matchCode % 255);
            } else
                *token += (BYTE)(matchCode);
        }
        /* Ensure we have enough space for the last literals. */
        assert(!(outputDirective == fillOutput && op + 1 + LASTLITERALS > olimit));

        anchor = ip;

        /* Test end of chunk */
        if (ip >= mflimitPlusOne) break;

        /* Fill table */
        {   U32 const h = LZ4_hashPosition(ip-2, tableType);
            if (tableType == byPtr) {
                LZ4_putPositionOnHash(ip-2, h, cctx->hashTable, byPtr);
            } else {
                U32 const idx = (U32)((ip-2) - base);
                LZ4_putIndexOnHash(idx, h, cctx->hashTable, tableType);
        }   }

        /* Test next position */
        if (tableType == byPtr) {

            match = LZ4_getPosition(ip, cctx->hashTable, tableType);
            LZ4_putPosition(ip, cctx->hashTable, tableType);
            if ( (match+LZ4_DISTANCE_MAX >= ip)
              && (LZ4_read32(match) == LZ4_read32(ip)) )
            { token=op++; *token=0; goto _next_match; }

        } else {   /* byU32, byU16 */

            U32 const h = LZ4_hashPosition(ip, tableType);
            U32 const current = (U32)(ip-base);
            U32 matchIndex = LZ4_getIndexOnHash(h, cctx->hashTable, tableType);
            assert(matchIndex < current);
            if (dictDirective == usingDictCtx) {
                if (matchIndex < startIndex) {
                    /* there was no match, try the dictionary */
                    assert(tableType == byU32);
                    matchIndex = LZ4_getIndexOnHash(h, dictCtx->hashTable, byU32);
                    match = dictBase + matchIndex;
                    lowLimit = dictionary;   /* required for match length counter */
                    matchIndex += dictDelta;
                } else {
                    match = base + matchIndex;
                    lowLimit = (const BYTE*)source;  /* required for match length counter */
                }
            } else if (dictDirective==usingExtDict) {
                if (matchIndex < startIndex) {
                    assert(dictBase);
                    match = dictBase + matchIndex;
                    lowLimit = dictionary;   /* required for match length counter */
                } else {
                    match = base + matchIndex;
                    lowLimit = (const BYTE*)source;   /* required for match length counter */
                }
            } else {   /* single memory segment */
                match = base + matchIndex;
            }
            LZ4_putIndexOnHash(current, h, cctx->hashTable, tableType);
            assert(matchIndex < current);
            if ( ((dictIssue==dictSmall) ? (matchIndex >= prefixIdxLimit) : 1)
              && (((tableType==byU16) && (LZ4_DISTANCE_MAX == LZ4_DISTANCE_ABSOLUTE_MAX)) ? 1 : (matchIndex+LZ4_DISTANCE_MAX >= current))
              && (LZ4_read32(match) == LZ4_read32(ip)) ) {
                token=op++;
                *token=0;
                if (maybe_extMem) offset = current - matchIndex;
                DEBUGLOG(6, "seq.start:%i, literals=%u, match.start:%i",
                            (int)(anchor-(const BYTE*)source), 0, (int)(ip-(const BYTE*)source));
                goto _next_match;
            }
        }

        /* Prepare next loop */
        forwardH = LZ4_hashPosition(++ip, tableType);

    }

_last_literals:
    /* Encode Last Literals */
    {   size_t lastRun = (size_t)(iend - anchor);
        if ( (outputDirective) &&  /* Check output buffer overflow */
            (op + lastRun + 1 + ((lastRun+255-RUN_MASK)/255) > olimit)) {
            if (outputDirective == fillOutput) {
                /* adapt lastRun to fill 'dst' */
                assert(olimit >= op);
                lastRun  = (size_t)(olimit-op) - 1/*token*/;
                lastRun -= (lastRun + 256 - RUN_MASK) / 256;  /*additional length tokens*/
            } else {
                assert(outputDirective == limitedOutput);
                return 0;   /* cannot compress within `dst` budget. Stored indexes in hash table are nonetheless fine */
            }
        }
        DEBUGLOG(6, "Final literal run : %i literals", (int)lastRun);
        if (lastRun >= RUN_MASK) {
            size_t accumulator = lastRun - RUN_MASK;
            *op++ = RUN_MASK << ML_BITS;
            for(; accumulator >= 255 ; accumulator-=255) *op++ = 255;
            *op++ = (BYTE) accumulator;
        } else {
            *op++ = (BYTE)(lastRun<<ML_BITS);
        }
        LZ4_memcpy(op, anchor, lastRun);
        ip = anchor + lastRun;
        op += lastRun;
    }

    if (outputDirective == fillOutput) {
        *inputConsumed = (int) (((const char*)ip)-source);
    }
    result = (int)(((char*)op) - dest);
    assert(result > 0);
    DEBUGLOG(5, "LZ4_compress_generic: compressed %i bytes into %i bytes", inputSize, result);
    return result;
}

/** LZ4_compress_generic() :
 *  inlined, to ensure branches are decided at compilation time;
 *  takes care of src == (NULL, 0)
 *  and forward the rest to LZ4_compress_generic_validated */
LZ4_FORCE_INLINE int LZ4_compress_generic(
                 LZ4_stream_t_internal* const cctx,
                 const char* const src,
                 char* const dst,
                 const int srcSize,
                 int *inputConsumed, /* only written when outputDirective == fillOutput */
                 const int dstCapacity,
                 const limitedOutput_directive outputDirective,
                 const tableType_t tableType,
                 const dict_directive dictDirective,
                 const dictIssue_directive dictIssue,
                 const int acceleration)
/* This wrapper takes care of the srcSize==0 empty-block case and otherwise
 * forwards to LZ4_compress_generic_validated.  srcSize is pinned to
 * {0,11,12}: 0 exercises the empty-block path, {11,12} satisfy the callee's
 * inputSize precondition.  noDict with a NULL dictionary and a zero
 * currentOffset keeps the forwarded call in bounds. */
__CPROVER_requires(srcSize == 0 || srcSize == 11 || srcSize == 12)
__CPROVER_requires(tableType == byPtr || tableType == byU32 || tableType == byU16)
__CPROVER_requires(dictDirective == noDict)
__CPROVER_requires(acceleration >= 1)
__CPROVER_requires(outputDirective == notLimited
                || outputDirective == limitedOutput
                || outputDirective == fillOutput)
__CPROVER_requires(dstCapacity >= 0 && dstCapacity <= AVOCADO_SPEC_CGV_MAXOUT)
__CPROVER_requires(__CPROVER_is_fresh(cctx, sizeof(LZ4_stream_t_internal)))
__CPROVER_requires(cctx->currentOffset == 0)
__CPROVER_requires(cctx->dictionary == NULL)
__CPROVER_requires(srcSize != 0 ==> __CPROVER_is_fresh(src, (size_t)srcSize))
__CPROVER_requires(__CPROVER_is_fresh(dst, AVOCADO_SPEC_CGV_DSTCAP))
__CPROVER_requires(outputDirective == fillOutput
                ==> __CPROVER_is_fresh(inputConsumed, sizeof(int)))
__CPROVER_assigns(cctx->dictSize, cctx->currentOffset, cctx->tableType)
__CPROVER_assigns(__CPROVER_object_upto(dst, (size_t)srcSize + 1))
__CPROVER_assigns(outputDirective == fillOutput:
                  __CPROVER_object_upto(inputConsumed, sizeof(int)))
/* Empty-block path (srcSize == 0) */
__CPROVER_ensures(
    (srcSize == 0 && outputDirective != notLimited && dstCapacity <= 0)
        ==> __CPROVER_return_value == 0)
__CPROVER_ensures(
    (srcSize == 0 && (outputDirective == notLimited || dstCapacity >= 1))
        ==> __CPROVER_return_value == 1)
__CPROVER_ensures(
    (srcSize == 0 && (outputDirective == notLimited || dstCapacity >= 1))
        ==> ((BYTE*)dst)[0] == 0)
__CPROVER_ensures(
    (srcSize == 0 && outputDirective == fillOutput && dstCapacity >= 1)
        ==> *inputConsumed == 0)
/* Forward path (srcSize in {11,12}): mirrors the validated contract */
__CPROVER_ensures(
    (srcSize >= 11 && outputDirective == fillOutput && dstCapacity == 0)
        ==> __CPROVER_return_value == 0)
__CPROVER_ensures(
    (srcSize >= 11 && outputDirective == limitedOutput && dstCapacity < srcSize + 1)
        ==> __CPROVER_return_value == 0)
__CPROVER_ensures(
    (srcSize >= 11
     && (outputDirective == notLimited
         || (outputDirective == limitedOutput && dstCapacity >= srcSize + 1)
         || (outputDirective == fillOutput && dstCapacity >= srcSize + 1)))
        ==> __CPROVER_return_value == srcSize + 1)
__CPROVER_ensures(
    (srcSize >= 11 && outputDirective == fillOutput && dstCapacity >= 1
      && dstCapacity < srcSize + 1)
        ==> __CPROVER_return_value == dstCapacity)
__CPROVER_ensures(
    (srcSize >= 11 && !(outputDirective == fillOutput && dstCapacity == 0))
        ==> cctx->tableType == (U32)tableType)
__CPROVER_ensures(
    (srcSize >= 11 && !(outputDirective == fillOutput && dstCapacity == 0))
        ==> cctx->currentOffset == (U32)srcSize)
__CPROVER_ensures(
    (srcSize >= 11 && !(outputDirective == fillOutput && dstCapacity == 0))
        ==> cctx->dictSize == __CPROVER_old(cctx->dictSize) + (U32)srcSize)
__CPROVER_ensures(
    (srcSize >= 11 && outputDirective == fillOutput && dstCapacity == 0)
        ==> cctx->currentOffset == 0)
__CPROVER_ensures(
    (srcSize >= 11
     && (outputDirective == notLimited
         || (outputDirective == limitedOutput && dstCapacity >= srcSize + 1)
         || (outputDirective == fillOutput && dstCapacity >= srcSize + 1)))
        ==> ((BYTE*)dst)[0] == (BYTE)((unsigned)srcSize << ML_BITS))
__CPROVER_ensures(
    (srcSize >= 11
     && (outputDirective == notLimited
         || (outputDirective == limitedOutput && dstCapacity >= srcSize + 1)
         || (outputDirective == fillOutput && dstCapacity >= srcSize + 1)))
        ==> __CPROVER_forall {
                int i;
                (i >= 0 && i < srcSize)
                    ==> ((BYTE*)dst)[i + 1] == ((const BYTE*)src)[i] })
__CPROVER_ensures(
    (srcSize >= 11 && outputDirective == fillOutput && dstCapacity >= 1
      && dstCapacity < srcSize + 1)
        ==> ((BYTE*)dst)[0] == (BYTE)((unsigned)(dstCapacity - 1) << ML_BITS))
__CPROVER_ensures(
    (srcSize >= 11 && outputDirective == fillOutput && dstCapacity >= 1
      && dstCapacity < srcSize + 1)
        ==> __CPROVER_forall {
                int j;
                (j >= 0 && j < dstCapacity - 1)
                    ==> ((BYTE*)dst)[j + 1] == ((const BYTE*)src)[j] })
__CPROVER_ensures(
    (srcSize >= 11 && outputDirective == fillOutput && dstCapacity >= srcSize + 1)
        ==> *inputConsumed == srcSize)
__CPROVER_ensures(
    (srcSize >= 11 && outputDirective == fillOutput && dstCapacity >= 1
      && dstCapacity < srcSize + 1)
        ==> *inputConsumed == dstCapacity - 1)
{
    DEBUGLOG(5, "LZ4_compress_generic: srcSize=%i, dstCapacity=%i",
                srcSize, dstCapacity);

    if ((U32)srcSize > (U32)LZ4_MAX_INPUT_SIZE) { return 0; }  /* Unsupported srcSize, too large (or negative) */
    if (srcSize == 0) {   /* src == NULL supported if srcSize == 0 */
        if (outputDirective != notLimited && dstCapacity <= 0) return 0;  /* no output, can't write anything */
        DEBUGLOG(5, "Generating an empty block");
        assert(outputDirective == notLimited || dstCapacity >= 1);
        assert(dst != NULL);
        dst[0] = 0;
        if (outputDirective == fillOutput) {
            assert (inputConsumed != NULL);
            *inputConsumed = 0;
        }
        return 1;
    }
    assert(src != NULL);

    return LZ4_compress_generic_validated(cctx, src, dst, srcSize,
                inputConsumed, /* only written into if outputDirective == fillOutput */
                dstCapacity, outputDirective,
                tableType, dictDirective, dictIssue, acceleration);
}


#define AVOCADO_SPEC_FEXT_CTX (&((LZ4_stream_t*)state)->internal_donotuse)
/* Alignment of LZ4_stream_t, computed via the classic offset-in-struct trick.
 * Defined here (before this function) because the equivalent
 * AVOCADO_SPEC_INITSTREAM_ALIGNED macro is only introduced later in the file,
 * next to LZ4_initStream. */
typedef struct { char avocado_spec_c; LZ4_stream_t avocado_spec_t; } avocado_spec_fext_align_s;
#define AVOCADO_SPEC_FEXT_ALIGN \
    (sizeof(avocado_spec_fext_align_s) - sizeof(LZ4_stream_t))
#define AVOCADO_SPEC_FEXT_ALIGNED(p) \
    (((size_t)(p) & (AVOCADO_SPEC_FEXT_ALIGN - 1)) == 0)


int LZ4_compress_fast_extState(void* state, const char* source, char* dest, int inputSize, int maxOutputSize, int acceleration)
/* Pin inputSize to {0,11,12}: 0 drives the empty-block path; {11,12} satisfy
 * LZ4_compress_generic's forward precondition.  state must be a fresh, aligned
 * LZ4_stream_t so LZ4_initStream returns it (non-NULL) and zeroes its internal
 * (currentOffset/dictionary==0, satisfying compress_generic's preconditions).
 * maxOutputSize stays in [0,20] (limitedOutput, dstCapacity<=CGV_MAXOUT) or
 * >=inputSize+16 (notLimited): the gap would pass an out-of-range dstCapacity. */
__CPROVER_requires(inputSize == 0 || inputSize == 11 || inputSize == 12)
__CPROVER_requires(__CPROVER_is_fresh(state, sizeof(LZ4_stream_t)))
__CPROVER_requires(AVOCADO_SPEC_FEXT_ALIGNED(state))
__CPROVER_requires(__CPROVER_is_fresh(dest, AVOCADO_SPEC_CGV_DSTCAP))
__CPROVER_requires(inputSize != 0 ==> __CPROVER_is_fresh(source, (size_t)inputSize))
__CPROVER_requires(maxOutputSize >= 0)
__CPROVER_requires(maxOutputSize <= AVOCADO_SPEC_CGV_MAXOUT
                || (maxOutputSize >= inputSize + 16 && maxOutputSize <= 64))
__CPROVER_assigns(__CPROVER_object_upto(state, sizeof(LZ4_stream_t_internal)))
__CPROVER_assigns(__CPROVER_object_upto(dest, (size_t)inputSize + 1))
/* return value */
__CPROVER_ensures(inputSize == 0 && maxOutputSize == 0 ==> __CPROVER_return_value == 0)
__CPROVER_ensures(inputSize == 0 && maxOutputSize >= 1 ==> __CPROVER_return_value == 1)
__CPROVER_ensures(inputSize == 11 && maxOutputSize < 12 ==> __CPROVER_return_value == 0)
__CPROVER_ensures(inputSize == 11 && maxOutputSize >= 12 ==> __CPROVER_return_value == 12)
__CPROVER_ensures(inputSize == 12 && maxOutputSize < 13 ==> __CPROVER_return_value == 0)
__CPROVER_ensures(inputSize == 12 && maxOutputSize >= 13 ==> __CPROVER_return_value == 13)
/* destination bytes on a successful compression */
__CPROVER_ensures(inputSize == 0 && maxOutputSize >= 1 ==> ((BYTE*)dest)[0] == 0)
__CPROVER_ensures(inputSize == 11 && maxOutputSize >= 12
    ==> ((BYTE*)dest)[0] == (BYTE)((unsigned)11 << ML_BITS))
__CPROVER_ensures(inputSize == 12 && maxOutputSize >= 13
    ==> ((BYTE*)dest)[0] == (BYTE)((unsigned)12 << ML_BITS))
__CPROVER_ensures(inputSize == 11 && maxOutputSize >= 12
    ==> __CPROVER_forall { int avocado_spec_i;
            (avocado_spec_i >= 0 && avocado_spec_i < 11)
                ==> ((BYTE*)dest)[avocado_spec_i + 1]
                    == ((const BYTE*)source)[avocado_spec_i] })
__CPROVER_ensures(inputSize == 12 && maxOutputSize >= 13
    ==> __CPROVER_forall { int avocado_spec_j;
            (avocado_spec_j >= 0 && avocado_spec_j < 12)
                ==> ((BYTE*)dest)[avocado_spec_j + 1]
                    == ((const BYTE*)source)[avocado_spec_j] })
/* stream state after a forward compression (table set to byU16) */
__CPROVER_ensures((inputSize == 11 || inputSize == 12)
    ==> AVOCADO_SPEC_FEXT_CTX->tableType == (U32)byU16)
__CPROVER_ensures((inputSize == 11 || inputSize == 12)
    ==> AVOCADO_SPEC_FEXT_CTX->currentOffset == (U32)inputSize)
__CPROVER_ensures((inputSize == 11 || inputSize == 12)
    ==> AVOCADO_SPEC_FEXT_CTX->dictSize == (U32)inputSize)
{
    LZ4_stream_t_internal* const ctx = & LZ4_initStream(state, sizeof(LZ4_stream_t)) -> internal_donotuse;
    assert(ctx != NULL);
    if (acceleration < 1) acceleration = LZ4_ACCELERATION_DEFAULT;
    if (acceleration > LZ4_ACCELERATION_MAX) acceleration = LZ4_ACCELERATION_MAX;
    if (maxOutputSize >= LZ4_compressBound(inputSize)) {
        if (inputSize < LZ4_64Klimit) {
            return LZ4_compress_generic(ctx, source, dest, inputSize, NULL, 0, notLimited, byU16, noDict, noDictIssue, acceleration);
        } else {
            const tableType_t tableType = ((sizeof(void*)==4) && ((uptrval)source > LZ4_DISTANCE_MAX)) ? byPtr : byU32;
            return LZ4_compress_generic(ctx, source, dest, inputSize, NULL, 0, notLimited, tableType, noDict, noDictIssue, acceleration);
        }
    } else {
        if (inputSize < LZ4_64Klimit) {
            return LZ4_compress_generic(ctx, source, dest, inputSize, NULL, maxOutputSize, limitedOutput, byU16, noDict, noDictIssue, acceleration);
        } else {
            const tableType_t tableType = ((sizeof(void*)==4) && ((uptrval)source > LZ4_DISTANCE_MAX)) ? byPtr : byU32;
            return LZ4_compress_generic(ctx, source, dest, inputSize, NULL, maxOutputSize, limitedOutput, tableType, noDict, noDictIssue, acceleration);
        }
    }
}

/**
 * LZ4_compress_fast_extState_fastReset() :
 * A variant of LZ4_compress_fast_extState().
 *
 * Using this variant avoids an expensive initialization step. It is only safe
 * to call if the state buffer is known to be correctly initialized already
 * (see comment in lz4.h on LZ4_resetStream_fast() for a definition of
 * "correctly initialized").
 */
int LZ4_compress_fast_extState_fastReset(void* state, const char* src, char* dst, int srcSize, int dstCapacity, int acceleration)
/* srcSize is pinned to {11,12}: both exercise the all-literals forward path
 * of LZ4_compress_generic (inputSize in [11,12] < LZ4_minLength) while the
 * byU16 branch (srcSize < LZ4_64Klimit) is taken.  A zero currentOffset at
 * entry makes LZ4_prepareTable leave it zero, so the else (noDictIssue) call
 * satisfies LZ4_compress_generic's currentOffset==0 precondition.
 * dstCapacity is either <= AVOCADO_SPEC_CGV_MAXOUT (limitedOutput branch, the
 * value forwarded to the callee) or >= compressBound == srcSize+16 (notLimited
 * branch, which forwards a literal 0). */
__CPROVER_requires(srcSize == 11 || srcSize == 12)
__CPROVER_requires(dstCapacity >= 0)
__CPROVER_requires(dstCapacity <= AVOCADO_SPEC_CGV_MAXOUT || dstCapacity >= srcSize + 16)
__CPROVER_requires(__CPROVER_is_fresh(state, sizeof(LZ4_stream_t)))
__CPROVER_requires(((LZ4_stream_t*)state)->internal_donotuse.currentOffset == 0)
__CPROVER_requires(__CPROVER_is_fresh(src, (size_t)srcSize))
__CPROVER_requires(__CPROVER_is_fresh(dst, AVOCADO_SPEC_CGV_DSTCAP))
__CPROVER_assigns(__CPROVER_object_whole(&((LZ4_stream_t*)state)->internal_donotuse))
__CPROVER_assigns(__CPROVER_object_upto(dst, (size_t)srcSize + 1))
/* Return value: srcSize+1 whenever there is room for the single literal block,
 * 0 otherwise (limitedOutput with too small a capacity). */
__CPROVER_ensures(dstCapacity >= srcSize + 1 ==> __CPROVER_return_value == srcSize + 1)
__CPROVER_ensures(dstCapacity < srcSize + 1 ==> __CPROVER_return_value == 0)
/* Output content on the success paths. */
__CPROVER_ensures(dstCapacity >= srcSize + 1 ==>
    ((BYTE*)dst)[0] == (BYTE)((unsigned)srcSize << ML_BITS))
__CPROVER_ensures(dstCapacity >= srcSize + 1 ==>
    __CPROVER_forall {
        int i;
        (i >= 0 && i < srcSize)
            ==> ((BYTE*)dst)[i + 1] == ((const BYTE*)src)[i] })
/* Context state is always updated to the byU16 table with currentOffset and
 * dictSize advanced by srcSize (fillOutput is never used, so this always holds). */
__CPROVER_ensures(((LZ4_stream_t*)state)->internal_donotuse.tableType == (U32)byU16)
__CPROVER_ensures(((LZ4_stream_t*)state)->internal_donotuse.currentOffset == (U32)srcSize)
__CPROVER_ensures(((LZ4_stream_t*)state)->internal_donotuse.dictSize == (U32)srcSize)
{
    LZ4_stream_t_internal* const ctx = &((LZ4_stream_t*)state)->internal_donotuse;
    if (acceleration < 1) acceleration = LZ4_ACCELERATION_DEFAULT;
    if (acceleration > LZ4_ACCELERATION_MAX) acceleration = LZ4_ACCELERATION_MAX;
    assert(ctx != NULL);

    if (dstCapacity >= LZ4_compressBound(srcSize)) {
        if (srcSize < LZ4_64Klimit) {
            const tableType_t tableType = byU16;
            LZ4_prepareTable(ctx, srcSize, tableType);
            if (ctx->currentOffset) {
                return LZ4_compress_generic(ctx, src, dst, srcSize, NULL, 0, notLimited, tableType, noDict, dictSmall, acceleration);
            } else {
                return LZ4_compress_generic(ctx, src, dst, srcSize, NULL, 0, notLimited, tableType, noDict, noDictIssue, acceleration);
            }
        } else {
            const tableType_t tableType = ((sizeof(void*)==4) && ((uptrval)src > LZ4_DISTANCE_MAX)) ? byPtr : byU32;
            LZ4_prepareTable(ctx, srcSize, tableType);
            return LZ4_compress_generic(ctx, src, dst, srcSize, NULL, 0, notLimited, tableType, noDict, noDictIssue, acceleration);
        }
    } else {
        if (srcSize < LZ4_64Klimit) {
            const tableType_t tableType = byU16;
            LZ4_prepareTable(ctx, srcSize, tableType);
            if (ctx->currentOffset) {
                return LZ4_compress_generic(ctx, src, dst, srcSize, NULL, dstCapacity, limitedOutput, tableType, noDict, dictSmall, acceleration);
            } else {
                return LZ4_compress_generic(ctx, src, dst, srcSize, NULL, dstCapacity, limitedOutput, tableType, noDict, noDictIssue, acceleration);
            }
        } else {
            const tableType_t tableType = ((sizeof(void*)==4) && ((uptrval)src > LZ4_DISTANCE_MAX)) ? byPtr : byU32;
            LZ4_prepareTable(ctx, srcSize, tableType);
            return LZ4_compress_generic(ctx, src, dst, srcSize, NULL, dstCapacity, limitedOutput, tableType, noDict, noDictIssue, acceleration);
        }
    }
}


int LZ4_compress_fast(const char* src, char* dest, int srcSize, int dstCapacity, int acceleration)
/* The stream state is created locally (LZ4_HEAPMODE==0 -> stack ctx), so only
 * src/dest/srcSize/dstCapacity cross the boundary.  Mirror
 * LZ4_compress_fast_extState's preconditions on those: srcSize pinned to
 * {0,11,12}, dest a fresh 64-byte buffer, source fresh when non-empty, and
 * dstCapacity either limitedOutput (<=20) or notLimited (>=srcSize+16, <=64). */
__CPROVER_requires(srcSize == 0 || srcSize == 11 || srcSize == 12)
__CPROVER_requires(__CPROVER_is_fresh(dest, AVOCADO_SPEC_CGV_DSTCAP))
__CPROVER_requires(srcSize != 0 ==> __CPROVER_is_fresh(src, (size_t)srcSize))
__CPROVER_requires(dstCapacity >= 0)
__CPROVER_requires(dstCapacity <= AVOCADO_SPEC_CGV_MAXOUT
                || (dstCapacity >= srcSize + 16 && dstCapacity <= 64))
__CPROVER_assigns(__CPROVER_object_upto(dest, (size_t)srcSize + 1))
/* return value */
__CPROVER_ensures(srcSize == 0 && dstCapacity == 0 ==> __CPROVER_return_value == 0)
__CPROVER_ensures(srcSize == 0 && dstCapacity >= 1 ==> __CPROVER_return_value == 1)
__CPROVER_ensures(srcSize == 11 && dstCapacity < 12 ==> __CPROVER_return_value == 0)
__CPROVER_ensures(srcSize == 11 && dstCapacity >= 12 ==> __CPROVER_return_value == 12)
__CPROVER_ensures(srcSize == 12 && dstCapacity < 13 ==> __CPROVER_return_value == 0)
__CPROVER_ensures(srcSize == 12 && dstCapacity >= 13 ==> __CPROVER_return_value == 13)
/* destination bytes on a successful compression */
__CPROVER_ensures(srcSize == 0 && dstCapacity >= 1 ==> ((BYTE*)dest)[0] == 0)
__CPROVER_ensures(srcSize == 11 && dstCapacity >= 12
    ==> ((BYTE*)dest)[0] == (BYTE)((unsigned)11 << ML_BITS))
__CPROVER_ensures(srcSize == 12 && dstCapacity >= 13
    ==> ((BYTE*)dest)[0] == (BYTE)((unsigned)12 << ML_BITS))
__CPROVER_ensures(srcSize == 11 && dstCapacity >= 12
    ==> __CPROVER_forall { int avocado_spec_i;
            (avocado_spec_i >= 0 && avocado_spec_i < 11)
                ==> ((BYTE*)dest)[avocado_spec_i + 1]
                    == ((const BYTE*)src)[avocado_spec_i] })
__CPROVER_ensures(srcSize == 12 && dstCapacity >= 13
    ==> __CPROVER_forall { int avocado_spec_j;
            (avocado_spec_j >= 0 && avocado_spec_j < 12)
                ==> ((BYTE*)dest)[avocado_spec_j + 1]
                    == ((const BYTE*)src)[avocado_spec_j] })
{
    int result;
#if (LZ4_HEAPMODE)
    LZ4_stream_t* const ctxPtr = (LZ4_stream_t*)ALLOC(sizeof(LZ4_stream_t));   /* malloc-calloc always properly aligned */
    if (ctxPtr == NULL) return 0;
#else
    LZ4_stream_t ctx;
    LZ4_stream_t* const ctxPtr = &ctx;
#endif
    result = LZ4_compress_fast_extState(ctxPtr, src, dest, srcSize, dstCapacity, acceleration);

#if (LZ4_HEAPMODE)
    FREEMEM(ctxPtr);
#endif
    return result;
}


int LZ4_compress_default(const char* src, char* dst, int srcSize, int dstCapacity)
__CPROVER_requires(srcSize == 0 || srcSize == 11 || srcSize == 12)
__CPROVER_requires(__CPROVER_is_fresh(dst, AVOCADO_SPEC_CGV_DSTCAP))
__CPROVER_requires(srcSize != 0 ==> __CPROVER_is_fresh(src, (size_t)srcSize))
__CPROVER_requires(dstCapacity >= 0)
__CPROVER_requires(dstCapacity <= AVOCADO_SPEC_CGV_MAXOUT
                || (dstCapacity >= srcSize + 16 && dstCapacity <= 64))
__CPROVER_assigns(__CPROVER_object_upto(dst, (size_t)srcSize + 1))
/* return value */
__CPROVER_ensures(srcSize == 0 && dstCapacity == 0 ==> __CPROVER_return_value == 0)
__CPROVER_ensures(srcSize == 0 && dstCapacity >= 1 ==> __CPROVER_return_value == 1)
__CPROVER_ensures(srcSize == 11 && dstCapacity < 12 ==> __CPROVER_return_value == 0)
__CPROVER_ensures(srcSize == 11 && dstCapacity >= 12 ==> __CPROVER_return_value == 12)
__CPROVER_ensures(srcSize == 12 && dstCapacity < 13 ==> __CPROVER_return_value == 0)
__CPROVER_ensures(srcSize == 12 && dstCapacity >= 13 ==> __CPROVER_return_value == 13)
/* destination bytes on a successful compression */
__CPROVER_ensures(srcSize == 0 && dstCapacity >= 1 ==> ((BYTE*)dst)[0] == 0)
__CPROVER_ensures(srcSize == 11 && dstCapacity >= 12
    ==> ((BYTE*)dst)[0] == (BYTE)((unsigned)11 << ML_BITS))
__CPROVER_ensures(srcSize == 12 && dstCapacity >= 13
    ==> ((BYTE*)dst)[0] == (BYTE)((unsigned)12 << ML_BITS))
__CPROVER_ensures(srcSize == 11 && dstCapacity >= 12
    ==> __CPROVER_forall { int avocado_spec_i;
            (avocado_spec_i >= 0 && avocado_spec_i < 11)
                ==> ((BYTE*)dst)[avocado_spec_i + 1]
                    == ((const BYTE*)src)[avocado_spec_i] })
__CPROVER_ensures(srcSize == 12 && dstCapacity >= 13
    ==> __CPROVER_forall { int avocado_spec_j;
            (avocado_spec_j >= 0 && avocado_spec_j < 12)
                ==> ((BYTE*)dst)[avocado_spec_j + 1]
                    == ((const BYTE*)src)[avocado_spec_j] })
{
    return LZ4_compress_fast(src, dst, srcSize, dstCapacity, 1);
}


/* Note!: This function leaves the stream in an unclean/broken state!
 * It is not safe to subsequently use the same state with a _fastReset() or
 * _continue() call without resetting it. */
static int LZ4_compress_destSize_extState_internal(LZ4_stream_t* state, const char* src, char* dst, int* srcSizePtr, int targetDstSize, int acceleration)
/* srcSize (*srcSizePtr) is pinned to {0,11,12}: 0 drives the empty-block path,
 * {11,12} satisfy the forward preconditions of both callees; all stay below
 * LZ4_64Klimit, so the byU16 branch is always taken.  state must be a fresh,
 * aligned LZ4_stream_t so LZ4_initStream returns it (non-NULL, assert passes).
 * targetDstSize is kept either <= AVOCADO_SPEC_CGV_MAXOUT (compress_generic's
 * fillOutput branch, dstCapacity in range) or in [srcSize+16, 64]
 * (compress_fast_extState branch).  The gap (CGV_MAXOUT, srcSize+16) is excluded
 * so no out-of-range dstCapacity reaches a callee.
 * The compressed result is always min(targetDstSize, srcSize+1) bytes, whose
 * token byte is (result-1)<<ML_BITS and whose literals mirror src; srcSizePtr
 * is left holding result-1 (bytes consumed) on every path that writes output. */
__CPROVER_requires(__CPROVER_is_fresh(state, sizeof(LZ4_stream_t)))
__CPROVER_requires(AVOCADO_SPEC_FEXT_ALIGNED(state))
__CPROVER_requires(__CPROVER_is_fresh(srcSizePtr, sizeof(int)))
__CPROVER_requires(*srcSizePtr == 0 || *srcSizePtr == 11 || *srcSizePtr == 12)
__CPROVER_requires(__CPROVER_is_fresh(dst, AVOCADO_SPEC_CGV_DSTCAP))
__CPROVER_requires(*srcSizePtr != 0 ==> __CPROVER_is_fresh(src, (size_t)*srcSizePtr))
__CPROVER_requires(acceleration >= 1)
__CPROVER_requires(targetDstSize >= 0)
__CPROVER_requires(targetDstSize <= AVOCADO_SPEC_CGV_MAXOUT
                || (targetDstSize >= *srcSizePtr + 16 && targetDstSize <= 64))
__CPROVER_assigns(__CPROVER_object_upto(state, sizeof(LZ4_stream_t_internal)))
__CPROVER_assigns(__CPROVER_object_upto(dst, (size_t)(*srcSizePtr) + 1))
__CPROVER_assigns(*srcSizePtr)
/* return value: compressed size is min(targetDstSize, srcSize+1) on every path */
__CPROVER_ensures(
    __CPROVER_return_value ==
        (targetDstSize < __CPROVER_old(*srcSizePtr) + 1
             ? targetDstSize : __CPROVER_old(*srcSizePtr) + 1))
/* token byte of the produced block */
__CPROVER_ensures(
    __CPROVER_return_value >= 1
        ==> ((BYTE*)dst)[0] == (BYTE)((unsigned)(__CPROVER_return_value - 1) << ML_BITS))
/* literals copied verbatim after the token byte */
__CPROVER_ensures(
    __CPROVER_return_value >= 1
        ==> __CPROVER_forall {
                int avocado_spec_i;
                (avocado_spec_i >= 0 && avocado_spec_i < __CPROVER_return_value - 1)
                    ==> ((BYTE*)dst)[avocado_spec_i + 1]
                        == ((const BYTE*)src)[avocado_spec_i] })
/* bytes consumed written back through srcSizePtr */
__CPROVER_ensures(
    __CPROVER_return_value >= 1
        ==> *srcSizePtr == __CPROVER_return_value - 1)
/* stream state after a forward compression (table set to byU16) */
__CPROVER_ensures(
    (__CPROVER_old(*srcSizePtr) == 11 || __CPROVER_old(*srcSizePtr) == 12)
     && __CPROVER_return_value >= 1
        ==> state->internal_donotuse.tableType == (U32)byU16)
__CPROVER_ensures(
    (__CPROVER_old(*srcSizePtr) == 11 || __CPROVER_old(*srcSizePtr) == 12)
     && __CPROVER_return_value >= 1
        ==> state->internal_donotuse.currentOffset == (U32)__CPROVER_old(*srcSizePtr))
__CPROVER_ensures(
    (__CPROVER_old(*srcSizePtr) == 11 || __CPROVER_old(*srcSizePtr) == 12)
     && __CPROVER_return_value >= 1
        ==> state->internal_donotuse.dictSize == (U32)__CPROVER_old(*srcSizePtr))
__CPROVER_ensures(
    (__CPROVER_old(*srcSizePtr) == 11 || __CPROVER_old(*srcSizePtr) == 12)
     && __CPROVER_return_value == 0
        ==> state->internal_donotuse.currentOffset == 0)
{
    void* const s = LZ4_initStream(state, sizeof (*state));
    assert(s != NULL); (void)s;

    if (targetDstSize >= LZ4_compressBound(*srcSizePtr)) {  /* compression success is guaranteed */
        return LZ4_compress_fast_extState(state, src, dst, *srcSizePtr, targetDstSize, acceleration);
    } else {
        if (*srcSizePtr < LZ4_64Klimit) {
            return LZ4_compress_generic(&state->internal_donotuse, src, dst, *srcSizePtr, srcSizePtr, targetDstSize, fillOutput, byU16, noDict, noDictIssue, acceleration);
        } else {
            tableType_t const addrMode = ((sizeof(void*)==4) && ((uptrval)src > LZ4_DISTANCE_MAX)) ? byPtr : byU32;
            return LZ4_compress_generic(&state->internal_donotuse, src, dst, *srcSizePtr, srcSizePtr, targetDstSize, fillOutput, addrMode, noDict, noDictIssue, acceleration);
    }   }
}

int LZ4_compress_destSize_extState(void* state, const char* src, char* dst, int* srcSizePtr, int targetDstSize, int acceleration)
/* Thin wrapper: forwards to LZ4_compress_destSize_extState_internal then cleans
 * the stream with LZ4_initStream(state, sizeof(LZ4_stream_t)).  The trailing
 * clean-up only re-touches `state`, so the internal's result / dst / srcSizePtr
 * postconditions are preserved and mirrored here; the final stream contents are
 * left unconstrained (LZ4_initStream's contract does not promise zeroing). */
__CPROVER_requires(__CPROVER_is_fresh(state, sizeof(LZ4_stream_t)))
__CPROVER_requires(AVOCADO_SPEC_FEXT_ALIGNED(state))
__CPROVER_requires(__CPROVER_is_fresh(srcSizePtr, sizeof(int)))
__CPROVER_requires(*srcSizePtr == 0 || *srcSizePtr == 11 || *srcSizePtr == 12)
__CPROVER_requires(__CPROVER_is_fresh(dst, AVOCADO_SPEC_CGV_DSTCAP))
__CPROVER_requires(*srcSizePtr != 0 ==> __CPROVER_is_fresh(src, (size_t)*srcSizePtr))
__CPROVER_requires(acceleration >= 1)
__CPROVER_requires(targetDstSize >= 0)
__CPROVER_requires(targetDstSize <= AVOCADO_SPEC_CGV_MAXOUT
                || (targetDstSize >= *srcSizePtr + 16 && targetDstSize <= 64))
__CPROVER_assigns(__CPROVER_object_upto(state, sizeof(LZ4_stream_t_internal)))
__CPROVER_assigns(__CPROVER_object_upto(dst, (size_t)(*srcSizePtr) + 1))
__CPROVER_assigns(*srcSizePtr)
/* return value: compressed size is min(targetDstSize, srcSize+1) on every path */
__CPROVER_ensures(
    __CPROVER_return_value ==
        (targetDstSize < __CPROVER_old(*srcSizePtr) + 1
             ? targetDstSize : __CPROVER_old(*srcSizePtr) + 1))
/* token byte of the produced block */
__CPROVER_ensures(
    __CPROVER_return_value >= 1
        ==> ((BYTE*)dst)[0] == (BYTE)((unsigned)(__CPROVER_return_value - 1) << ML_BITS))
/* literals copied verbatim after the token byte */
__CPROVER_ensures(
    __CPROVER_return_value >= 1
        ==> __CPROVER_forall {
                int avocado_spec_i;
                (avocado_spec_i >= 0 && avocado_spec_i < __CPROVER_return_value - 1)
                    ==> ((BYTE*)dst)[avocado_spec_i + 1]
                        == ((const BYTE*)src)[avocado_spec_i] })
/* bytes consumed written back through srcSizePtr */
__CPROVER_ensures(
    __CPROVER_return_value >= 1
        ==> *srcSizePtr == __CPROVER_return_value - 1)
{
    int const r = LZ4_compress_destSize_extState_internal((LZ4_stream_t*)state, src, dst, srcSizePtr, targetDstSize, acceleration);
    /* clean the state on exit */
    LZ4_initStream(state, sizeof (LZ4_stream_t));
    return r;
}


int LZ4_compress_destSize(const char* src, char* dst, int* srcSizePtr, int targetDstSize)
/* The stream state is created locally (LZ4_HEAPMODE==0 -> stack ctx), so only
 * src/dst/srcSizePtr/targetDstSize cross the boundary.  Mirror
 * LZ4_compress_destSize_extState_internal's preconditions on those (acceleration
 * is fixed to 1, which satisfies acceleration>=1): *srcSizePtr pinned to
 * {0,11,12}, dst a fresh 64-byte buffer, src fresh when non-empty, and
 * targetDstSize either <= CGV_MAXOUT or in [*srcSizePtr+16, 64]. */
__CPROVER_requires(__CPROVER_is_fresh(srcSizePtr, sizeof(int)))
__CPROVER_requires(*srcSizePtr == 0 || *srcSizePtr == 11 || *srcSizePtr == 12)
__CPROVER_requires(__CPROVER_is_fresh(dst, AVOCADO_SPEC_CGV_DSTCAP))
__CPROVER_requires(*srcSizePtr != 0 ==> __CPROVER_is_fresh(src, (size_t)*srcSizePtr))
__CPROVER_requires(targetDstSize >= 0)
__CPROVER_requires(targetDstSize <= AVOCADO_SPEC_CGV_MAXOUT
                || (targetDstSize >= *srcSizePtr + 16 && targetDstSize <= 64))
__CPROVER_assigns(__CPROVER_object_upto(dst, (size_t)(*srcSizePtr) + 1))
__CPROVER_assigns(*srcSizePtr)
/* return value: compressed size is min(targetDstSize, srcSize+1) on every path */
__CPROVER_ensures(
    __CPROVER_return_value ==
        (targetDstSize < __CPROVER_old(*srcSizePtr) + 1
             ? targetDstSize : __CPROVER_old(*srcSizePtr) + 1))
/* token byte of the produced block */
__CPROVER_ensures(
    __CPROVER_return_value >= 1
        ==> ((BYTE*)dst)[0] == (BYTE)((unsigned)(__CPROVER_return_value - 1) << ML_BITS))
/* literals copied verbatim after the token byte */
__CPROVER_ensures(
    __CPROVER_return_value >= 1
        ==> __CPROVER_forall {
                int avocado_spec_i;
                (avocado_spec_i >= 0 && avocado_spec_i < __CPROVER_return_value - 1)
                    ==> ((BYTE*)dst)[avocado_spec_i + 1]
                        == ((const BYTE*)src)[avocado_spec_i] })
/* bytes consumed written back through srcSizePtr */
__CPROVER_ensures(
    __CPROVER_return_value >= 1
        ==> *srcSizePtr == __CPROVER_return_value - 1)
{
#if (LZ4_HEAPMODE)
    LZ4_stream_t* const ctx = (LZ4_stream_t*)ALLOC(sizeof(LZ4_stream_t));   /* malloc-calloc always properly aligned */
    if (ctx == NULL) return 0;
#else
    LZ4_stream_t ctxBody;
    LZ4_stream_t* const ctx = &ctxBody;
#endif

    int result = LZ4_compress_destSize_extState_internal(ctx, src, dst, srcSizePtr, targetDstSize, 1);

#if (LZ4_HEAPMODE)
    FREEMEM(ctx);
#endif
    return result;
}



/*-******************************
*  Streaming functions
********************************/

#if !defined(LZ4_STATIC_LINKING_ONLY_DISABLE_MEMORY_ALLOCATION)
LZ4_stream_t* LZ4_createStream(void)
__CPROVER_assigns()
__CPROVER_ensures(
    __CPROVER_return_value == NULL
    || __CPROVER_is_fresh(__CPROVER_return_value, sizeof(LZ4_stream_t)))
{
    LZ4_stream_t* const lz4s = (LZ4_stream_t*)ALLOC(sizeof(LZ4_stream_t));
    LZ4_STATIC_ASSERT(sizeof(LZ4_stream_t) >= sizeof(LZ4_stream_t_internal));
    DEBUGLOG(4, "LZ4_createStream %p", lz4s);
    if (lz4s == NULL) return NULL;
    LZ4_initStream(lz4s, sizeof(*lz4s));
    return lz4s;
}
#endif

typedef struct { char c; LZ4_stream_t t; } avocado_spec_stream_t_a;

static size_t LZ4_stream_t_alignment(void)
__CPROVER_assigns()
__CPROVER_ensures(__CPROVER_return_value == sizeof(avocado_spec_stream_t_a) - sizeof(LZ4_stream_t))
__CPROVER_ensures(__CPROVER_return_value >= 1)
__CPROVER_ensures(__CPROVER_return_value <= sizeof(LZ4_stream_t))
{
#if LZ4_ALIGN_TEST
    typedef struct { char c; LZ4_stream_t t; } t_a;
    return sizeof(t_a) - sizeof(LZ4_stream_t);
#else
    return 1;  /* effectively disabled */
#endif
}

#define AVOCADO_SPEC_INITSTREAM_ALIGN \
    (sizeof(avocado_spec_stream_t_a) - sizeof(LZ4_stream_t))
#define AVOCADO_SPEC_INITSTREAM_ALIGNED(p) \
    (((size_t)(p) & (AVOCADO_SPEC_INITSTREAM_ALIGN - 1)) == 0)

LZ4_stream_t* LZ4_initStream (void* buffer, size_t size)
__CPROVER_requires(buffer == NULL || __CPROVER_is_fresh(buffer, sizeof(LZ4_stream_t)))
__CPROVER_requires(size <= sizeof(LZ4_stream_t))
__CPROVER_assigns(
    (buffer != NULL && size >= sizeof(LZ4_stream_t)) :
        __CPROVER_object_upto(buffer, sizeof(LZ4_stream_t_internal)))
__CPROVER_ensures(
    buffer == NULL ==> __CPROVER_return_value == NULL)
__CPROVER_ensures(
    (buffer != NULL && size < sizeof(LZ4_stream_t))
        ==> __CPROVER_return_value == NULL)
__CPROVER_ensures(
    (buffer != NULL && size >= sizeof(LZ4_stream_t)
        && !AVOCADO_SPEC_INITSTREAM_ALIGNED(buffer))
        ==> __CPROVER_return_value == NULL)
__CPROVER_ensures(
    (buffer != NULL && size >= sizeof(LZ4_stream_t)
        && AVOCADO_SPEC_INITSTREAM_ALIGNED(buffer))
        ==> __CPROVER_return_value == (LZ4_stream_t*)buffer)
{
    DEBUGLOG(5, "LZ4_initStream");
    if (buffer == NULL) { return NULL; }
    if (size < sizeof(LZ4_stream_t)) { return NULL; }
    if (!LZ4_isAligned(buffer, LZ4_stream_t_alignment())) return NULL;
    MEM_INIT(buffer, 0, sizeof(LZ4_stream_t_internal));
    return (LZ4_stream_t*)buffer;
}

/* resetStream is now deprecated,
 * prefer initStream() which is more general */
void LZ4_resetStream (LZ4_stream_t* LZ4_stream)
__CPROVER_requires(__CPROVER_is_fresh(LZ4_stream, sizeof(LZ4_stream_t)))
__CPROVER_assigns(__CPROVER_object_upto(LZ4_stream, sizeof(LZ4_stream_t_internal)))
__CPROVER_ensures(__CPROVER_forall {
    size_t avocado_spec_i;
    (avocado_spec_i < sizeof(LZ4_stream_t_internal))
        ==> ((unsigned char*)LZ4_stream)[avocado_spec_i] == 0
})
{
    DEBUGLOG(5, "LZ4_resetStream (ctx:%p)", LZ4_stream);
    MEM_INIT(LZ4_stream, 0, sizeof(LZ4_stream_t_internal));
}

/* LZ4_resetStream_fast calls LZ4_prepareTable(&ctx->internal_donotuse, 0, byU32).
 * With inputSize==0 and tableType==byU32, the table is reset exactly when the
 * previous table was not clearedTable AND (it was not byU32, or currentOffset > 1 GB). */
#define AVOCADO_SPEC_RESETFAST_RESET \
    ( (tableType_t)__CPROVER_old(ctx->internal_donotuse.tableType) != clearedTable \
      && ( (tableType_t)__CPROVER_old(ctx->internal_donotuse.tableType) != byU32 \
           || __CPROVER_old(ctx->internal_donotuse.currentOffset) > 1 GB ) )

void LZ4_resetStream_fast(LZ4_stream_t* ctx)
__CPROVER_requires(__CPROVER_is_fresh(ctx, sizeof(*ctx)))
__CPROVER_assigns(__CPROVER_object_whole(ctx->internal_donotuse.hashTable),
                  ctx->internal_donotuse.currentOffset,
                  ctx->internal_donotuse.tableType,
                  ctx->internal_donotuse.dictCtx,
                  ctx->internal_donotuse.dictionary,
                  ctx->internal_donotuse.dictSize)
__CPROVER_ensures(ctx->internal_donotuse.dictCtx == NULL)
__CPROVER_ensures(ctx->internal_donotuse.dictionary == NULL)
__CPROVER_ensures(ctx->internal_donotuse.dictSize == 0)
__CPROVER_ensures(AVOCADO_SPEC_RESETFAST_RESET ==>
                  ctx->internal_donotuse.tableType == (U32)clearedTable)
__CPROVER_ensures(!AVOCADO_SPEC_RESETFAST_RESET ==>
                  ctx->internal_donotuse.tableType
                  == __CPROVER_old(ctx->internal_donotuse.tableType))
__CPROVER_ensures(AVOCADO_SPEC_RESETFAST_RESET ==>
                  ctx->internal_donotuse.currentOffset == 0)
__CPROVER_ensures(!AVOCADO_SPEC_RESETFAST_RESET ==>
                  ctx->internal_donotuse.currentOffset ==
                  ( __CPROVER_old(ctx->internal_donotuse.currentOffset) != 0
                    ? (U32)(__CPROVER_old(ctx->internal_donotuse.currentOffset) + 64 KB)
                    : __CPROVER_old(ctx->internal_donotuse.currentOffset) ))
__CPROVER_ensures(AVOCADO_SPEC_RESETFAST_RESET ==>
                  (ctx->internal_donotuse.hashTable[0] == 0
                   && ctx->internal_donotuse.hashTable[1] == 0
                   && ctx->internal_donotuse.hashTable[2] == 0
                   && ctx->internal_donotuse.hashTable[3] == 0))
{
    LZ4_prepareTable(&(ctx->internal_donotuse), 0, byU32);
}

#if !defined(LZ4_STATIC_LINKING_ONLY_DISABLE_MEMORY_ALLOCATION)
int LZ4_freeStream (LZ4_stream_t* LZ4_stream)
__CPROVER_requires(LZ4_stream == NULL)
__CPROVER_ensures(__CPROVER_return_value == 0)
{
    if (!LZ4_stream) return 0;   /* support free on NULL */
    DEBUGLOG(5, "LZ4_freeStream %p", LZ4_stream);
    FREEMEM(LZ4_stream);
    return (0);
}
#endif


typedef enum { _ld_fast, _ld_slow } LoadDict_mode_e;
#define HASH_UNIT sizeof(reg_t)
#define AVOCADO_SPEC_LOADDICT_N 16
int LZ4_loadDict_internal(LZ4_stream_t* LZ4_dict,
                    const char* dictionary, int dictSize,
                    LoadDict_mode_e _ld)
__CPROVER_requires(_ld == _ld_fast || _ld == _ld_slow)
__CPROVER_requires(dictSize >= 0 && dictSize <= AVOCADO_SPEC_LOADDICT_N)
__CPROVER_requires(__CPROVER_is_fresh(LZ4_dict, sizeof(LZ4_stream_t)))
__CPROVER_requires(__CPROVER_is_fresh(dictionary, AVOCADO_SPEC_LOADDICT_N))
__CPROVER_assigns(__CPROVER_object_upto(LZ4_dict, sizeof(LZ4_stream_t_internal)))
__CPROVER_ensures(LZ4_dict->internal_donotuse.currentOffset == 64 KB)
__CPROVER_ensures(dictSize < (int)HASH_UNIT ==> __CPROVER_return_value == 0)
__CPROVER_ensures(dictSize < (int)HASH_UNIT ==> LZ4_dict->internal_donotuse.dictSize == 0)
__CPROVER_ensures(dictSize < (int)HASH_UNIT ==> LZ4_dict->internal_donotuse.dictionary == NULL)
__CPROVER_ensures(dictSize < (int)HASH_UNIT ==> LZ4_dict->internal_donotuse.tableType == clearedTable)
__CPROVER_ensures(dictSize >= (int)HASH_UNIT ==> __CPROVER_return_value == dictSize)
__CPROVER_ensures(dictSize >= (int)HASH_UNIT ==> LZ4_dict->internal_donotuse.dictSize == (U32)dictSize)
__CPROVER_ensures(dictSize >= (int)HASH_UNIT ==> LZ4_dict->internal_donotuse.dictionary == (const BYTE*)dictionary)
__CPROVER_ensures(dictSize >= (int)HASH_UNIT ==> LZ4_dict->internal_donotuse.tableType == byU32)
{
    LZ4_stream_t_internal* const dict = &LZ4_dict->internal_donotuse;
    const tableType_t tableType = byU32;
    const BYTE* p = (const BYTE*)dictionary;
    const BYTE* const dictEnd = p + dictSize;
    U32 idx32;

    DEBUGLOG(4, "LZ4_loadDict (%i bytes from %p into %p)", dictSize, dictionary, LZ4_dict);

    /* It's necessary to reset the context,
     * and not just continue it with prepareTable()
     * to avoid any risk of generating overflowing matchIndex
     * when compressing using this dictionary */
    LZ4_resetStream(LZ4_dict);

    /* We always increment the offset by 64 KB, since, if the dict is longer,
     * we truncate it to the last 64k, and if it's shorter, we still want to
     * advance by a whole window length so we can provide the guarantee that
     * there are only valid offsets in the window, which allows an optimization
     * in LZ4_compress_fast_continue() where it uses noDictIssue even when the
     * dictionary isn't a full 64k. */
    dict->currentOffset += 64 KB;

    if (dictSize < (int)HASH_UNIT) {
        return 0;
    }

    if ((dictEnd - p) > 64 KB) p = dictEnd - 64 KB;
    dict->dictionary = p;
    dict->dictSize = (U32)(dictEnd - p);
    dict->tableType = (U32)tableType;
    idx32 = dict->currentOffset - dict->dictSize;

    while (p <= dictEnd-HASH_UNIT) {
        U32 const h = LZ4_hashPosition(p, tableType);
        /* Note: overwriting => favors positions end of dictionary */
        LZ4_putIndexOnHash(idx32, h, dict->hashTable, tableType);
        p+=3; idx32+=3;
    }

    if (_ld == _ld_slow) {
        /* Fill hash table with additional references, to improve compression capability */
        p = dict->dictionary;
        idx32 = dict->currentOffset - dict->dictSize;
        while (p <= dictEnd-HASH_UNIT) {
            U32 const h = LZ4_hashPosition(p, tableType);
            U32 const limit = dict->currentOffset - 64 KB;
            if (LZ4_getIndexOnHash(h, dict->hashTable, tableType) <= limit) {
                /* Note: not overwriting => favors positions beginning of dictionary */
                LZ4_putIndexOnHash(idx32, h, dict->hashTable, tableType);
            }
            p++; idx32++;
        }
    }

    return (int)dict->dictSize;
}

int LZ4_loadDict(LZ4_stream_t* LZ4_dict, const char* dictionary, int dictSize)
__CPROVER_requires(dictSize >= 0 && dictSize <= AVOCADO_SPEC_LOADDICT_N)
__CPROVER_requires(__CPROVER_is_fresh(LZ4_dict, sizeof(LZ4_stream_t)))
__CPROVER_requires(__CPROVER_is_fresh(dictionary, AVOCADO_SPEC_LOADDICT_N))
__CPROVER_assigns(__CPROVER_object_upto(LZ4_dict, sizeof(LZ4_stream_t_internal)))
__CPROVER_ensures(LZ4_dict->internal_donotuse.currentOffset == 64 KB)
__CPROVER_ensures(dictSize < (int)HASH_UNIT ==> __CPROVER_return_value == 0)
__CPROVER_ensures(dictSize < (int)HASH_UNIT ==> LZ4_dict->internal_donotuse.dictSize == 0)
__CPROVER_ensures(dictSize < (int)HASH_UNIT ==> LZ4_dict->internal_donotuse.dictionary == NULL)
__CPROVER_ensures(dictSize < (int)HASH_UNIT ==> LZ4_dict->internal_donotuse.tableType == clearedTable)
__CPROVER_ensures(dictSize >= (int)HASH_UNIT ==> __CPROVER_return_value == dictSize)
__CPROVER_ensures(dictSize >= (int)HASH_UNIT ==> LZ4_dict->internal_donotuse.dictSize == (U32)dictSize)
__CPROVER_ensures(dictSize >= (int)HASH_UNIT ==> LZ4_dict->internal_donotuse.dictionary == (const BYTE*)dictionary)
__CPROVER_ensures(dictSize >= (int)HASH_UNIT ==> LZ4_dict->internal_donotuse.tableType == byU32)
{
    return LZ4_loadDict_internal(LZ4_dict, dictionary, dictSize, _ld_fast);
}

int LZ4_loadDictSlow(LZ4_stream_t* LZ4_dict, const char* dictionary, int dictSize)
__CPROVER_requires(dictSize >= 0 && dictSize <= AVOCADO_SPEC_LOADDICT_N)
__CPROVER_requires(__CPROVER_is_fresh(LZ4_dict, sizeof(LZ4_stream_t)))
__CPROVER_requires(__CPROVER_is_fresh(dictionary, AVOCADO_SPEC_LOADDICT_N))
__CPROVER_assigns(__CPROVER_object_upto(LZ4_dict, sizeof(LZ4_stream_t_internal)))
__CPROVER_ensures(LZ4_dict->internal_donotuse.currentOffset == 64 KB)
__CPROVER_ensures(dictSize < (int)HASH_UNIT ==> __CPROVER_return_value == 0)
__CPROVER_ensures(dictSize < (int)HASH_UNIT ==> LZ4_dict->internal_donotuse.dictSize == 0)
__CPROVER_ensures(dictSize < (int)HASH_UNIT ==> LZ4_dict->internal_donotuse.dictionary == NULL)
__CPROVER_ensures(dictSize < (int)HASH_UNIT ==> LZ4_dict->internal_donotuse.tableType == clearedTable)
__CPROVER_ensures(dictSize >= (int)HASH_UNIT ==> __CPROVER_return_value == dictSize)
__CPROVER_ensures(dictSize >= (int)HASH_UNIT ==> LZ4_dict->internal_donotuse.dictSize == (U32)dictSize)
__CPROVER_ensures(dictSize >= (int)HASH_UNIT ==> LZ4_dict->internal_donotuse.dictionary == (const BYTE*)dictionary)
__CPROVER_ensures(dictSize >= (int)HASH_UNIT ==> LZ4_dict->internal_donotuse.tableType == byU32)
{
    return LZ4_loadDict_internal(LZ4_dict, dictionary, dictSize, _ld_slow);
}

void LZ4_attach_dictionary(LZ4_stream_t* workingStream, const LZ4_stream_t* dictionaryStream)
__CPROVER_requires(__CPROVER_w_ok(workingStream, sizeof(*workingStream)))
__CPROVER_requires(dictionaryStream == NULL
                   || __CPROVER_r_ok(dictionaryStream, sizeof(*dictionaryStream)))
__CPROVER_assigns(workingStream->internal_donotuse.currentOffset,
                  workingStream->internal_donotuse.dictCtx)
__CPROVER_ensures(workingStream->internal_donotuse.currentOffset ==
    ( (dictionaryStream != NULL
       && __CPROVER_old(workingStream->internal_donotuse.currentOffset) == 0)
      ? (U32)(64 KB)
      : __CPROVER_old(workingStream->internal_donotuse.currentOffset) ))
__CPROVER_ensures(workingStream->internal_donotuse.dictCtx ==
    ( (dictionaryStream == NULL
       || dictionaryStream->internal_donotuse.dictSize == 0)
      ? NULL
      : &(dictionaryStream->internal_donotuse) ))
{
    const LZ4_stream_t_internal* dictCtx = (dictionaryStream == NULL) ? NULL :
        &(dictionaryStream->internal_donotuse);

    DEBUGLOG(4, "LZ4_attach_dictionary (%p, %p, size %u)",
             workingStream, dictionaryStream,
             dictCtx != NULL ? dictCtx->dictSize : 0);

    if (dictCtx != NULL) {
        /* If the current offset is zero, we will never look in the
         * external dictionary context, since there is no value a table
         * entry can take that indicate a miss. In that case, we need
         * to bump the offset to something non-zero.
         */
        if (workingStream->internal_donotuse.currentOffset == 0) {
            workingStream->internal_donotuse.currentOffset = 64 KB;
        }

        /* Don't actually attach an empty dictionary.
         */
        if (dictCtx->dictSize == 0) {
            dictCtx = NULL;
        }
    }
    workingStream->internal_donotuse.dictCtx = dictCtx;
}


#define AVOCADO_SPEC_RENORM_RESCALE \
  (((__CPROVER_old(LZ4_dict->currentOffset) + (unsigned)nextSize)) > 0x80000000)
#define AVOCADO_SPEC_RENORM_DELTA \
  (__CPROVER_old(LZ4_dict->currentOffset) - (U32)(64 KB))
#define AVOCADO_SPEC_RENORM_OLDHASH(i) (__CPROVER_old(LZ4_dict->hashTable[(i)]))
#define AVOCADO_SPEC_RENORM_NEWDS \
  (AVOCADO_SPEC_RENORM_RESCALE \
    ? (__CPROVER_old(LZ4_dict->dictSize) > (U32)(64 KB) \
        ? (U32)(64 KB) : __CPROVER_old(LZ4_dict->dictSize)) \
    : __CPROVER_old(LZ4_dict->dictSize))


static void LZ4_renormDictT(LZ4_stream_t_internal* LZ4_dict, int nextSize)
__CPROVER_requires(nextSize >= 0)
__CPROVER_requires(__CPROVER_is_fresh(LZ4_dict, sizeof(LZ4_stream_t_internal)))
__CPROVER_requires(LZ4_dict->dictSize <= (U32)(128 KB))
__CPROVER_assigns(LZ4_dict->hashTable, LZ4_dict->currentOffset,
                  LZ4_dict->dictSize, LZ4_dict->dictionary)
__CPROVER_ensures(LZ4_dict->currentOffset ==
  (AVOCADO_SPEC_RENORM_RESCALE ? (U32)(64 KB)
                               : __CPROVER_old(LZ4_dict->currentOffset)))
__CPROVER_ensures(LZ4_dict->dictSize == AVOCADO_SPEC_RENORM_NEWDS)
__CPROVER_ensures(LZ4_dict->dictionary ==
  (AVOCADO_SPEC_RENORM_RESCALE
    ? (__CPROVER_old(LZ4_dict->dictionary)
        + __CPROVER_old(LZ4_dict->dictSize) - AVOCADO_SPEC_RENORM_NEWDS)
    : __CPROVER_old(LZ4_dict->dictionary)))
__CPROVER_ensures(__CPROVER_forall {
  unsigned avocado_spec_i;
  (avocado_spec_i < LZ4_HASH_SIZE_U32) ==>
    (LZ4_dict->hashTable[avocado_spec_i] ==
      (AVOCADO_SPEC_RENORM_RESCALE
        ? (AVOCADO_SPEC_RENORM_OLDHASH(avocado_spec_i) < AVOCADO_SPEC_RENORM_DELTA
            ? 0u
            : AVOCADO_SPEC_RENORM_OLDHASH(avocado_spec_i) - AVOCADO_SPEC_RENORM_DELTA)
        : AVOCADO_SPEC_RENORM_OLDHASH(avocado_spec_i)))
})
{
    assert(nextSize >= 0);
    if (LZ4_dict->currentOffset + (unsigned)nextSize > 0x80000000) {   /* potential ptrdiff_t overflow (32-bits mode) */
        /* rescale hash table */
        U32 const delta = LZ4_dict->currentOffset - 64 KB;
        const BYTE* dictEnd = LZ4_dict->dictionary + LZ4_dict->dictSize;
        int i;
        DEBUGLOG(4, "LZ4_renormDictT");
        for (i=0; i<LZ4_HASH_SIZE_U32; i++) {
            if (LZ4_dict->hashTable[i] < delta) LZ4_dict->hashTable[i]=0;
            else LZ4_dict->hashTable[i] -= delta;
        }
        LZ4_dict->currentOffset = 64 KB;
        if (LZ4_dict->dictSize > 64 KB) LZ4_dict->dictSize = 64 KB;
        LZ4_dict->dictionary = dictEnd - LZ4_dict->dictSize;
    }
}


int LZ4_compress_fast_continue (LZ4_stream_t* LZ4_stream,
                                const char* source, char* dest,
                                int inputSize, int maxOutputSize,
                                int acceleration)
__CPROVER_requires(__CPROVER_is_fresh(LZ4_stream, sizeof(LZ4_stream_t)))
__CPROVER_requires(inputSize == 0 || inputSize == 11 || inputSize == 12)
__CPROVER_requires(maxOutputSize >= 0 && maxOutputSize <= AVOCADO_SPEC_CGV_MAXOUT)
__CPROVER_requires(acceleration >= 1)
__CPROVER_requires(__CPROVER_is_fresh(dest, AVOCADO_SPEC_CGV_DSTCAP))
__CPROVER_requires(inputSize != 0 ==> __CPROVER_is_fresh(source, (size_t)inputSize))
__CPROVER_requires(LZ4_stream->internal_donotuse.dictSize <= (U32)(64 KB))
__CPROVER_requires(LZ4_stream->internal_donotuse.dictionary == NULL)
__CPROVER_requires(LZ4_stream->internal_donotuse.dictCtx == NULL)
__CPROVER_requires(LZ4_stream->internal_donotuse.currentOffset == 0)
__CPROVER_assigns(__CPROVER_object_whole(LZ4_stream))
__CPROVER_assigns(__CPROVER_object_upto(dest, (size_t)inputSize + 1))
{
    const tableType_t tableType = byU32;
    LZ4_stream_t_internal* const streamPtr = &LZ4_stream->internal_donotuse;
    const char* dictEnd = streamPtr->dictSize ? (const char*)streamPtr->dictionary + streamPtr->dictSize : NULL;

    DEBUGLOG(5, "LZ4_compress_fast_continue (inputSize=%i, dictSize=%u)", inputSize, streamPtr->dictSize);

    LZ4_renormDictT(streamPtr, inputSize);   /* fix index overflow */
    if (acceleration < 1) acceleration = LZ4_ACCELERATION_DEFAULT;
    if (acceleration > LZ4_ACCELERATION_MAX) acceleration = LZ4_ACCELERATION_MAX;

    /* invalidate tiny dictionaries */
    if ( (streamPtr->dictSize < 4)     /* tiny dictionary : not enough for a hash */
      && (dictEnd != source)           /* prefix mode */
      && (inputSize > 0)               /* tolerance : don't lose history, in case next invocation would use prefix mode */
      && (streamPtr->dictCtx == NULL)  /* usingDictCtx */
      ) {
        DEBUGLOG(5, "LZ4_compress_fast_continue: dictSize(%u) at addr:%p is too small", streamPtr->dictSize, streamPtr->dictionary);
        /* remove dictionary existence from history, to employ faster prefix mode */
        streamPtr->dictSize = 0;
        streamPtr->dictionary = (const BYTE*)source;
        dictEnd = source;
    }

    /* Check overlapping input/dictionary space */
    {   const char* const sourceEnd = source + inputSize;
        if ((sourceEnd > (const char*)streamPtr->dictionary) && (sourceEnd < dictEnd)) {
            streamPtr->dictSize = (U32)(dictEnd - sourceEnd);
            if (streamPtr->dictSize > 64 KB) streamPtr->dictSize = 64 KB;
            if (streamPtr->dictSize < 4) streamPtr->dictSize = 0;
            streamPtr->dictionary = (const BYTE*)dictEnd - streamPtr->dictSize;
        }
    }

    /* prefix mode : source data follows dictionary */
    if (dictEnd == source) {
        if ((streamPtr->dictSize < 64 KB) && (streamPtr->dictSize < streamPtr->currentOffset))
            return LZ4_compress_generic(streamPtr, source, dest, inputSize, NULL, maxOutputSize, limitedOutput, tableType, withPrefix64k, dictSmall, acceleration);
        else
            return LZ4_compress_generic(streamPtr, source, dest, inputSize, NULL, maxOutputSize, limitedOutput, tableType, withPrefix64k, noDictIssue, acceleration);
    }

    /* external dictionary mode */
    {   int result;
        if (streamPtr->dictCtx) {
            /* We depend here on the fact that dictCtx'es (produced by
             * LZ4_loadDict) guarantee that their tables contain no references
             * to offsets between dictCtx->currentOffset - 64 KB and
             * dictCtx->currentOffset - dictCtx->dictSize. This makes it safe
             * to use noDictIssue even when the dict isn't a full 64 KB.
             */
            if (inputSize > 4 KB) {
                /* For compressing large blobs, it is faster to pay the setup
                 * cost to copy the dictionary's tables into the active context,
                 * so that the compression loop is only looking into one table.
                 */
                LZ4_memcpy(streamPtr, streamPtr->dictCtx, sizeof(*streamPtr));
                result = LZ4_compress_generic(streamPtr, source, dest, inputSize, NULL, maxOutputSize, limitedOutput, tableType, usingExtDict, noDictIssue, acceleration);
            } else {
                result = LZ4_compress_generic(streamPtr, source, dest, inputSize, NULL, maxOutputSize, limitedOutput, tableType, usingDictCtx, noDictIssue, acceleration);
            }
        } else {  /* small data <= 4 KB */
            if ((streamPtr->dictSize < 64 KB) && (streamPtr->dictSize < streamPtr->currentOffset)) {
                result = LZ4_compress_generic(streamPtr, source, dest, inputSize, NULL, maxOutputSize, limitedOutput, tableType, usingExtDict, dictSmall, acceleration);
            } else {
                result = LZ4_compress_generic(streamPtr, source, dest, inputSize, NULL, maxOutputSize, limitedOutput, tableType, usingExtDict, noDictIssue, acceleration);
            }
        }
        streamPtr->dictionary = (const BYTE*)source;
        streamPtr->dictSize = (U32)inputSize;
        return result;
    }
}


/* Hidden debug function, to force-test external dictionary mode */
int LZ4_compress_forceExtDict (LZ4_stream_t* LZ4_dict, const char* source, char* dest, int srcSize)
__CPROVER_requires(srcSize == 0 || srcSize == 11 || srcSize == 12)
__CPROVER_requires(__CPROVER_is_fresh(LZ4_dict, sizeof(LZ4_stream_t)))
__CPROVER_requires(LZ4_dict->internal_donotuse.dictSize <= (U32)(128 KB))
__CPROVER_requires(LZ4_dict->internal_donotuse.currentOffset == 0)
__CPROVER_requires(LZ4_dict->internal_donotuse.dictionary == NULL)
__CPROVER_requires(srcSize != 0 ==> __CPROVER_is_fresh(source, (size_t)srcSize))
__CPROVER_requires(__CPROVER_is_fresh(dest, (size_t)srcSize + 1))
__CPROVER_assigns(__CPROVER_object_whole(&LZ4_dict->internal_donotuse))
__CPROVER_assigns(__CPROVER_object_upto(dest, (size_t)srcSize + 1))
__CPROVER_ensures(srcSize == 0 ==> __CPROVER_return_value == 1)
__CPROVER_ensures(srcSize == 0 ==> ((BYTE*)dest)[0] == 0)
__CPROVER_ensures(srcSize >= 11 ==> __CPROVER_return_value == srcSize + 1)
__CPROVER_ensures(srcSize >= 11 ==>
    ((BYTE*)dest)[0] == (BYTE)((unsigned)srcSize << ML_BITS))
__CPROVER_ensures(srcSize >= 11 ==> __CPROVER_forall {
    int avocado_spec_i;
    (avocado_spec_i >= 0 && avocado_spec_i < srcSize)
        ==> ((BYTE*)dest)[avocado_spec_i + 1] == ((const BYTE*)source)[avocado_spec_i] })
__CPROVER_ensures(LZ4_dict->internal_donotuse.dictionary == (const BYTE*)source)
__CPROVER_ensures(LZ4_dict->internal_donotuse.dictSize == (U32)srcSize)
{
    LZ4_stream_t_internal* const streamPtr = &LZ4_dict->internal_donotuse;
    int result;

    LZ4_renormDictT(streamPtr, srcSize);

    if ((streamPtr->dictSize < 64 KB) && (streamPtr->dictSize < streamPtr->currentOffset)) {
        result = LZ4_compress_generic(streamPtr, source, dest, srcSize, NULL, 0, notLimited, byU32, usingExtDict, dictSmall, 1);
    } else {
        result = LZ4_compress_generic(streamPtr, source, dest, srcSize, NULL, 0, notLimited, byU32, usingExtDict, noDictIssue, 1);
    }

    streamPtr->dictionary = (const BYTE*)source;
    streamPtr->dictSize = (U32)srcSize;

    return result;
}


/*! LZ4_saveDict() :
 *  If previously compressed data block is not guaranteed to remain available at its memory location,
 *  save it into a safer place (char* safeBuffer).
 *  Note : no need to call LZ4_loadDict() afterwards, dictionary is immediately usable,
 *         one can therefore call LZ4_compress_fast_continue() right after.
 * @return : saved dictionary size in bytes (necessarily <= dictSize), or 0 if error.
 */
int LZ4_saveDict (LZ4_stream_t* LZ4_dict, char* safeBuffer, int dictSize)
__CPROVER_requires(__CPROVER_is_fresh(LZ4_dict, sizeof(LZ4_stream_t_internal)))
__CPROVER_requires(LZ4_dict->internal_donotuse.dictSize >= 1
                   && LZ4_dict->internal_donotuse.dictSize <= 16)
__CPROVER_requires(__CPROVER_is_fresh(LZ4_dict->internal_donotuse.dictionary,
                                      LZ4_dict->internal_donotuse.dictSize))
__CPROVER_requires(__CPROVER_is_fresh(safeBuffer,
                                      LZ4_dict->internal_donotuse.dictSize))
__CPROVER_requires(dictSize >= 0 && dictSize <= 32)
__CPROVER_assigns(LZ4_dict->internal_donotuse.dictionary,
                  LZ4_dict->internal_donotuse.dictSize,
                  __CPROVER_object_upto(safeBuffer,
                                        (size_t)LZ4_dict->internal_donotuse.dictSize))
__CPROVER_ensures(__CPROVER_return_value ==
                  (__CPROVER_old(dictSize)
                       <= (int)__CPROVER_old(LZ4_dict->internal_donotuse.dictSize)
                   ? __CPROVER_old(dictSize)
                   : (int)__CPROVER_old(LZ4_dict->internal_donotuse.dictSize)))
__CPROVER_ensures(LZ4_dict->internal_donotuse.dictSize == (U32)__CPROVER_return_value)
__CPROVER_ensures(LZ4_dict->internal_donotuse.dictionary == (const BYTE*)safeBuffer)
{
    LZ4_stream_t_internal* const dict = &LZ4_dict->internal_donotuse;

    DEBUGLOG(5, "LZ4_saveDict : dictSize=%i, safeBuffer=%p", dictSize, safeBuffer);

    if ((U32)dictSize > 64 KB) { dictSize = 64 KB; } /* useless to define a dictionary > 64 KB */
    if ((U32)dictSize > dict->dictSize) { dictSize = (int)dict->dictSize; }

    if (safeBuffer == NULL) assert(dictSize == 0);
    if (dictSize > 0) {
        const BYTE* const previousDictEnd = dict->dictionary + dict->dictSize;
        assert(dict->dictionary);
        LZ4_memmove(safeBuffer, previousDictEnd - dictSize, (size_t)dictSize);
    }

    dict->dictionary = (const BYTE*)safeBuffer;
    dict->dictSize = (U32)dictSize;

    return dictSize;
}



/*-*******************************
 *  Decompression functions
 ********************************/

typedef enum { decode_full_block = 0, partial_decode = 1 } earlyEnd_directive;

#undef MIN
#define MIN(a,b)    ( (a) < (b) ? (a) : (b) )


/* variant for decompress_unsafe()
 * does not know end of input
 * presumes input is well formed
 * note : will consume at least one byte */
#define AVOCADO_SPEC_RLL_N 8


/* variant for decompress_unsafe()
 * does not know end of input
 * presumes input is well formed
 * note : will consume at least one byte */
static size_t read_long_length_no_check(const BYTE** pp)
__CPROVER_requires(__CPROVER_is_fresh(pp, sizeof(const BYTE*)))
__CPROVER_requires(__CPROVER_is_fresh(*pp, AVOCADO_SPEC_RLL_N))
__CPROVER_requires((*pp)[AVOCADO_SPEC_RLL_N - 1] != 255)
__CPROVER_assigns(*pp)
__CPROVER_ensures(*pp > __CPROVER_old(*pp))
__CPROVER_ensures(*pp <= __CPROVER_old(*pp) + AVOCADO_SPEC_RLL_N)
__CPROVER_ensures((*pp)[-1] != 255)
__CPROVER_ensures(__CPROVER_return_value ==
    (size_t)255 * ((size_t)(*pp - __CPROVER_old(*pp)) - 1) + (size_t)(*pp)[-1])
{
    size_t b, l = 0;
    do { b = **pp; (*pp)++; l += b; } while (b==255);
    DEBUGLOG(6, "read_long_length_no_check: +length=%zu using %zu input bytes", l, l/255 + 1)
    return l;
}

/* core decoder variant for LZ4_decompress_fast*()
 * for legacy support only : these entry points are deprecated.
 * - Presumes input is correctly formed (no defense vs malformed inputs)
 * - Does not know input size (presume input buffer is "large enough")
 * - Decompress a full block (only)
 * @return : nb of bytes read from input.
 * Note : this variant is not optimized for speed, just for maintenance.
 *        the goal is to remove support of decompress_fast*() variants by v2.0
**/
#define AVOCADO_SPEC_DUG_OUT 20
#define AVOCADO_SPEC_DUG_IN  64

/* core decoder variant for LZ4_decompress_fast*()
 * for legacy support only : these entry points are deprecated.
 * - Presumes input is correctly formed (no defense vs malformed inputs)
 * - Does not know input size (presume input buffer is "large enough")
 * - Decompress a full block (only)
 * @return : nb of bytes read from input.
 * Note : this variant is not optimized for speed, just for maintenance.
 *        the goal is to remove support of decompress_fast*() variants by v2.0
**/
LZ4_FORCE_INLINE int
LZ4_decompress_unsafe_generic(
                 const BYTE* const istart,
                 BYTE* const ostart,
                 int decompressedSize,

                 size_t prefixSize,
                 const BYTE* const dictStart,  /* only if dict==usingExtDict */
                 const size_t dictSize         /* note: =0 if dictStart==NULL */
                 )
__CPROVER_requires(decompressedSize > 0 && decompressedSize <= AVOCADO_SPEC_DUG_OUT)
__CPROVER_requires(prefixSize == 0)
__CPROVER_requires(dictStart == NULL)
__CPROVER_requires(dictSize == 0)
__CPROVER_requires(__CPROVER_is_fresh(istart, AVOCADO_SPEC_DUG_IN))
__CPROVER_requires(__CPROVER_is_fresh(ostart, (size_t)decompressedSize))
__CPROVER_assigns(__CPROVER_object_upto(ostart, (size_t)decompressedSize))
__CPROVER_ensures(__CPROVER_return_value == -1 ||
    (__CPROVER_return_value >= 1 && __CPROVER_return_value <= AVOCADO_SPEC_DUG_IN))
{
    const BYTE* ip = istart;
    BYTE* op = (BYTE*)ostart;
    BYTE* const oend = ostart + decompressedSize;
    const BYTE* const prefixStart = ostart - prefixSize;

    DEBUGLOG(5, "LZ4_decompress_unsafe_generic");
    if (dictStart == NULL) assert(dictSize == 0);

    while (1) {
        /* start new sequence */
        unsigned token = *ip++;

        /* literals */
        {   size_t ll = token >> ML_BITS;
            if (ll==15) {
                /* long literal length */
                ll += read_long_length_no_check(&ip);
            }
            if ((size_t)(oend-op) < ll) return -1; /* output buffer overflow */
            LZ4_memmove(op, ip, ll); /* support in-place decompression */
            op += ll;
            ip += ll;
            if ((size_t)(oend-op) < MFLIMIT) {
                if (op==oend) break;  /* end of block */
                DEBUGLOG(5, "invalid: literals end at distance %zi from end of block", oend-op);
                /* incorrect end of block :
                 * last match must start at least MFLIMIT==12 bytes before end of output block */
                return -1;
        }   }

        /* match */
        {   size_t ml = token & 15;
            size_t const offset = LZ4_readLE16(ip);
            ip+=2;

            if (ml==15) {
                /* long literal length */
                ml += read_long_length_no_check(&ip);
            }
            ml += MINMATCH;

            if ((size_t)(oend-op) < ml) return -1; /* output buffer overflow */

            {   const BYTE* match = op - offset;

                /* out of range */
                if (offset > (size_t)(op - prefixStart) + dictSize) {
                    DEBUGLOG(6, "offset out of range");
                    return -1;
                }

                /* check special case : extDict */
                if (offset > (size_t)(op - prefixStart)) {
                    /* extDict scenario */
                    const BYTE* const dictEnd = dictStart + dictSize;
                    const BYTE* extMatch = dictEnd - (offset - (size_t)(op-prefixStart));
                    size_t const extml = (size_t)(dictEnd - extMatch);
                    if (extml > ml) {
                        /* match entirely within extDict */
                        LZ4_memmove(op, extMatch, ml);
                        op += ml;
                        ml = 0;
                    } else {
                        /* match split between extDict & prefix */
                        LZ4_memmove(op, extMatch, extml);
                        op += extml;
                        ml -= extml;
                    }
                    match = prefixStart;
                }

                /* match copy - slow variant, supporting overlap copy */
                {   size_t u;
                    for (u=0; u<ml; u++) {
                        op[u] = match[u];
            }   }   }
            op += ml;
            if ((size_t)(oend-op) < LASTLITERALS) {
                DEBUGLOG(5, "invalid: match ends at distance %zi from end of block", oend-op);
                /* incorrect end of block :
                 * last match must stop at least LASTLITERALS==5 bytes before end of output block */
                return -1;
            }
        } /* match */
    } /* main loop */
    return (int)(ip - istart);
}


/* Read the variable-length literal or match length.
 *
 * @ip : input pointer
 * @ilimit : position after which if length is not decoded, the input is necessarily corrupted.
 * @initial_check - check ip >= ipmax before start of loop.  Returns initial_error if so.
 * @error (output) - error code.  Must be set to 0 before call.
**/
typedef size_t Rvl_t;
static const Rvl_t rvl_error = (Rvl_t)(-1);
#define AVOCADO_SPEC_RVL_N 8
LZ4_FORCE_INLINE Rvl_t
read_variable_length(const BYTE** ip, const BYTE* ilimit,
                     int initial_check)
__CPROVER_requires(__CPROVER_is_fresh(ip, sizeof(*ip)))
__CPROVER_requires(__CPROVER_is_fresh(*ip, AVOCADO_SPEC_RVL_N))
__CPROVER_requires(__CPROVER_same_object(ilimit, *ip))
__CPROVER_requires(__CPROVER_pointer_offset(ilimit) < __CPROVER_pointer_offset(*ip) + AVOCADO_SPEC_RVL_N)
__CPROVER_assigns(*ip)
/* initial-check error path: *ip is left unchanged */
__CPROVER_ensures(
    (initial_check && __CPROVER_old(*ip) >= ilimit)
    ==> (__CPROVER_return_value == rvl_error
         && *ip == __CPROVER_old(*ip)))
/* passed initial check, first byte < 255, still within limit: exact result */
__CPROVER_ensures(
    ((!initial_check || __CPROVER_old(*ip) < ilimit)
     && __CPROVER_old(**ip) != 255
     && __CPROVER_old(*ip) + 1 <= ilimit)
    ==> (__CPROVER_return_value == (Rvl_t)__CPROVER_old(**ip)
         && *ip == __CPROVER_old(*ip) + 1))
/* passed initial check, but first increment hits the limit: error */
__CPROVER_ensures(
    ((!initial_check || __CPROVER_old(*ip) < ilimit)
     && __CPROVER_old(*ip) + 1 > ilimit)
    ==> (__CPROVER_return_value == rvl_error
         && *ip == __CPROVER_old(*ip) + 1))
/* *ip only ever advances, and by at most the buffer bound */
__CPROVER_ensures(*ip >= __CPROVER_old(*ip))
__CPROVER_ensures(*ip <= __CPROVER_old(*ip) + AVOCADO_SPEC_RVL_N)
{
    Rvl_t s, length = 0;
    assert(ip != NULL);
    assert(*ip !=  NULL);
    assert(ilimit != NULL);
    if (initial_check && unlikely((*ip) >= ilimit)) {    /* read limit reached */
        return rvl_error;
    }
    s = **ip;
    (*ip)++;
    length += s;
    if (unlikely((*ip) > ilimit)) {    /* read limit reached */
        return rvl_error;
    }
    /* accumulator overflow detection (32-bit mode only) */
    if ((sizeof(length) < 8) && unlikely(length > ((Rvl_t)(-1)/2)) ) {
        return rvl_error;
    }
    if (likely(s != 255)) return length;
    do {
        s = **ip;
        (*ip)++;
        length += s;
        if (unlikely((*ip) > ilimit)) {    /* read limit reached */
            return rvl_error;
        }
        /* accumulator overflow detection (32-bit mode only) */
        if ((sizeof(length) < 8) && unlikely(length > ((Rvl_t)(-1)/2)) ) {
            return rvl_error;
        }
    } while (s == 255);

    return length;
}

/*! LZ4_decompress_generic() :
 *  This generic decompression function covers all use cases.
 *  It shall be instantiated several times, using different sets of directives.
 *  Note that it is important for performance that this function really get inlined,
 *  in order to remove useless branches during compilation optimization.
 */
LZ4_FORCE_INLINE int
LZ4_decompress_generic(
                 const char* const src,
                 char* const dst,
                 int srcSize,
                 int outputSize,         /* If endOnInput==endOnInputSize, this value is `dstCapacity` */

                 earlyEnd_directive partialDecoding,  /* full, partial */
                 dict_directive dict,                 /* noDict, withPrefix64k, usingExtDict */
                 const BYTE* const lowPrefix,  /* always <= dst, == dst when no prefix */
                 const BYTE* const dictStart,  /* only if dict==usingExtDict */
                 const size_t dictSize         /* note : = 0 if noDict */
                 )
/* clang-format off */
__CPROVER_requires(srcSize >= 0 && srcSize <= 16)
__CPROVER_requires(outputSize >= 0 && outputSize <= 16)
__CPROVER_requires(src == NULL || __CPROVER_r_ok(src, (size_t)srcSize + AVOCADO_SPEC_RVL_N))
__CPROVER_requires(__CPROVER_w_ok(dst, (size_t)outputSize))
__CPROVER_requires(__CPROVER_same_object(lowPrefix, (const BYTE*)dst))
__CPROVER_requires(__CPROVER_pointer_offset(lowPrefix) <= __CPROVER_pointer_offset((const BYTE*)dst))
__CPROVER_requires(dictSize <= 16)
__CPROVER_requires(dictStart == NULL || __CPROVER_r_ok(dictStart, dictSize))
__CPROVER_assigns(__CPROVER_object_upto(dst, (size_t)outputSize))
__CPROVER_ensures(__CPROVER_return_value <= outputSize)
__CPROVER_ensures(((src == NULL) || (outputSize < 0)) ==> __CPROVER_return_value == -1)
__CPROVER_ensures((__CPROVER_old(src) != NULL && outputSize == 0) ==>
                  (__CPROVER_return_value == 0 || __CPROVER_return_value == -1))
/* clang-format on */
{
    if ((src == NULL) || (outputSize < 0)) { return -1; }

    {   const BYTE* ip = (const BYTE*) src;
        const BYTE* const iend = ip + srcSize;

        BYTE* op = (BYTE*) dst;
        BYTE* const oend = op + outputSize;
        BYTE* cpy;

        const BYTE* const dictEnd = (dictStart == NULL) ? NULL : dictStart + dictSize;

        const int checkOffset = (dictSize < (int)(64 KB));


        /* Set up the "end" pointers for the shortcut. */
        const BYTE* const shortiend = iend - 14 /*maxLL*/ - 2 /*offset*/;
        const BYTE* const shortoend = oend - 14 /*maxLL*/ - 18 /*maxML*/;

        const BYTE* match;
        size_t offset;
        unsigned token;
        size_t length;


        DEBUGLOG(5, "LZ4_decompress_generic (srcSize:%i, dstSize:%i)", srcSize, outputSize);

        /* Special cases */
        assert(lowPrefix <= op);
        if (unlikely(outputSize==0)) {
            /* Empty output buffer */
            if (partialDecoding) return 0;
            return ((srcSize==1) && (*ip==0)) ? 0 : -1;
        }
        if (unlikely(srcSize==0)) { return -1; }

    /* LZ4_FAST_DEC_LOOP:
     * designed for modern OoO performance cpus,
     * where copying reliably 32-bytes is preferable to an unpredictable branch.
     * note : fast loop may show a regression for some client arm chips. */
#if LZ4_FAST_DEC_LOOP
        if ((oend - op) < FASTLOOP_SAFE_DISTANCE) {
            DEBUGLOG(6, "move to safe decode loop");
            goto safe_decode;
        }

        /* Fast loop : decode sequences as long as output < oend-FASTLOOP_SAFE_DISTANCE */
        DEBUGLOG(6, "using fast decode loop");
        while (1) {
            /* Main fastloop assertion: We can always wildcopy FASTLOOP_SAFE_DISTANCE */
            assert(oend - op >= FASTLOOP_SAFE_DISTANCE);
            assert(ip < iend);
            token = *ip++;
            length = token >> ML_BITS;  /* literal length */
            DEBUGLOG(7, "blockPos%6u: litLength token = %u", (unsigned)(op-(BYTE*)dst), (unsigned)length);

            /* decode literal length */
            if (length == RUN_MASK) {
                size_t const addl = read_variable_length(&ip, iend-RUN_MASK, 1);
                if (addl == rvl_error) {
                    DEBUGLOG(6, "error reading long literal length");
                    goto _output_error;
                }
                length += addl;
                if (unlikely((uptrval)(op)+length<(uptrval)(op))) { goto _output_error; } /* overflow detection */
                if (unlikely((uptrval)(ip)+length<(uptrval)(ip))) { goto _output_error; } /* overflow detection */

                /* copy literals */
                LZ4_STATIC_ASSERT(MFLIMIT >= WILDCOPYLENGTH);
                if ((op+length>oend-32) || (ip+length>iend-32)) { goto safe_literal_copy; }
                LZ4_wildCopy32(op, ip, op+length);
                ip += length; op += length;
            } else if (ip <= iend-(16 + 1/*max lit + offset + nextToken*/)) {
                /* We don't need to check oend, since we check it once for each loop below */
                DEBUGLOG(7, "copy %u bytes in a 16-bytes stripe", (unsigned)length);
                /* Literals can only be <= 14, but hope compilers optimize better when copy by a register size */
                LZ4_memcpy(op, ip, 16);
                ip += length; op += length;
            } else {
                goto safe_literal_copy;
            }

            /* get offset */
            offset = LZ4_readLE16(ip); ip+=2;
            DEBUGLOG(6, "blockPos%6u: offset = %u", (unsigned)(op-(BYTE*)dst), (unsigned)offset);
            match = op - offset;
            assert(match <= op);  /* overflow check */

            /* get matchlength */
            length = token & ML_MASK;
            DEBUGLOG(7, "  match length token = %u (len==%u)", (unsigned)length, (unsigned)length+MINMATCH);

            if (length == ML_MASK) {
                size_t const addl = read_variable_length(&ip, iend - LASTLITERALS + 1, 0);
                if (addl == rvl_error) {
                    DEBUGLOG(5, "error reading long match length");
                    goto _output_error;
                }
                length += addl;
                length += MINMATCH;
                DEBUGLOG(7, "  long match length == %u", (unsigned)length);
                if (unlikely((uptrval)(op)+length<(uptrval)op)) { goto _output_error; } /* overflow detection */
                if (op + length >= oend - FASTLOOP_SAFE_DISTANCE) {
                    goto safe_match_copy;
                }
            } else {
                length += MINMATCH;
                if (op + length >= oend - FASTLOOP_SAFE_DISTANCE) {
                    DEBUGLOG(7, "moving to safe_match_copy (ml==%u)", (unsigned)length);
                    goto safe_match_copy;
                }

                /* Fastpath check: skip LZ4_wildCopy32 when true */
                if ((dict == withPrefix64k) || (match >= lowPrefix)) {
                    if (offset >= 8) {
                        assert(match >= lowPrefix);
                        assert(match <= op);
                        assert(op + 18 <= oend);

                        LZ4_memcpy(op, match, 8);
                        LZ4_memcpy(op+8, match+8, 8);
                        LZ4_memcpy(op+16, match+16, 2);
                        op += length;
                        continue;
            }   }   }

            if ( checkOffset && (unlikely(match + dictSize < lowPrefix)) ) {
                DEBUGLOG(5, "Error : pos=%zi, offset=%zi => outside buffers", op-lowPrefix, op-match);
                goto _output_error;
            }
            /* match starting within external dictionary */
            if ((dict==usingExtDict) && (match < lowPrefix)) {
                assert(dictEnd != NULL);
                if (unlikely(op+length > oend-LASTLITERALS)) {
                    if (partialDecoding) {
                        DEBUGLOG(7, "partialDecoding: dictionary match, close to dstEnd");
                        length = MIN(length, (size_t)(oend-op));
                    } else {
                        DEBUGLOG(6, "end-of-block condition violated")
                        goto _output_error;
                }   }

                if (length <= (size_t)(lowPrefix-match)) {
                    /* match fits entirely within external dictionary : just copy */
                    LZ4_memmove(op, dictEnd - (lowPrefix-match), length);
                    op += length;
                } else {
                    /* match stretches into both external dictionary and current block */
                    size_t const copySize = (size_t)(lowPrefix - match);
                    size_t const restSize = length - copySize;
                    LZ4_memcpy(op, dictEnd - copySize, copySize);
                    op += copySize;
                    if (restSize > (size_t)(op - lowPrefix)) {  /* overlap copy */
                        BYTE* const endOfMatch = op + restSize;
                        const BYTE* copyFrom = lowPrefix;
                        while (op < endOfMatch) { *op++ = *copyFrom++; }
                    } else {
                        LZ4_memcpy(op, lowPrefix, restSize);
                        op += restSize;
                }   }
                continue;
            }

            /* copy match within block */
            cpy = op + length;

            assert((op <= oend) && (oend-op >= 32));
            if (unlikely(offset<16)) {
                LZ4_memcpy_using_offset(op, match, cpy, offset);
            } else {
                LZ4_wildCopy32(op, match, cpy);
            }

            op = cpy;   /* wildcopy correction */
        }
    safe_decode:
#endif

        /* Main Loop : decode remaining sequences where output < FASTLOOP_SAFE_DISTANCE */
        DEBUGLOG(6, "using safe decode loop");
        while (1) {
            assert(ip < iend);
            token = *ip++;
            length = token >> ML_BITS;  /* literal length */
            DEBUGLOG(7, "blockPos%6u: litLength token = %u", (unsigned)(op-(BYTE*)dst), (unsigned)length);

            /* A two-stage shortcut for the most common case:
             * 1) If the literal length is 0..14, and there is enough space,
             * enter the shortcut and copy 16 bytes on behalf of the literals
             * (in the fast mode, only 8 bytes can be safely copied this way).
             * 2) Further if the match length is 4..18, copy 18 bytes in a similar
             * manner; but we ensure that there's enough space in the output for
             * those 18 bytes earlier, upon entering the shortcut (in other words,
             * there is a combined check for both stages).
             */
            if ( (length != RUN_MASK)
                /* strictly "less than" on input, to re-enter the loop with at least one byte */
              && likely((ip < shortiend) & (op <= shortoend)) ) {
                /* Copy the literals */
                LZ4_memcpy(op, ip, 16);
                op += length; ip += length;

                /* The second stage: prepare for match copying, decode full info.
                 * If it doesn't work out, the info won't be wasted. */
                length = token & ML_MASK; /* match length */
                DEBUGLOG(7, "blockPos%6u: matchLength token = %u (len=%u)", (unsigned)(op-(BYTE*)dst), (unsigned)length, (unsigned)length + 4);
                offset = LZ4_readLE16(ip); ip += 2;
                match = op - offset;
                assert(match <= op); /* check overflow */

                /* Do not deal with overlapping matches. */
                if ( (length != ML_MASK)
                  && (offset >= 8)
                  && (dict==withPrefix64k || match >= lowPrefix) ) {
                    /* Copy the match. */
                    LZ4_memcpy(op + 0, match + 0, 8);
                    LZ4_memcpy(op + 8, match + 8, 8);
                    LZ4_memcpy(op +16, match +16, 2);
                    op += length + MINMATCH;
                    /* Both stages worked, load the next token. */
                    continue;
                }

                /* The second stage didn't work out, but the info is ready.
                 * Propel it right to the point of match copying. */
                goto _copy_match;
            }

            /* decode literal length */
            if (length == RUN_MASK) {
                size_t const addl = read_variable_length(&ip, iend-RUN_MASK, 1);
                if (addl == rvl_error) { goto _output_error; }
                length += addl;
                if (unlikely((uptrval)(op)+length<(uptrval)(op))) { goto _output_error; } /* overflow detection */
                if (unlikely((uptrval)(ip)+length<(uptrval)(ip))) { goto _output_error; } /* overflow detection */
            }

#if LZ4_FAST_DEC_LOOP
        safe_literal_copy:
#endif
            /* copy literals */
            cpy = op+length;

            LZ4_STATIC_ASSERT(MFLIMIT >= WILDCOPYLENGTH);
            if ((cpy>oend-MFLIMIT) || (ip+length>iend-(2+1+LASTLITERALS))) {
                /* We've either hit the input parsing restriction or the output parsing restriction.
                 * In the normal scenario, decoding a full block, it must be the last sequence,
                 * otherwise it's an error (invalid input or dimensions).
                 * In partialDecoding scenario, it's necessary to ensure there is no buffer overflow.
                 */
                if (partialDecoding) {
                    /* Since we are partial decoding we may be in this block because of the output parsing
                     * restriction, which is not valid since the output buffer is allowed to be undersized.
                     */
                    DEBUGLOG(7, "partialDecoding: copying literals, close to input or output end")
                    DEBUGLOG(7, "partialDecoding: literal length = %u", (unsigned)length);
                    DEBUGLOG(7, "partialDecoding: remaining space in dstBuffer : %i", (int)(oend - op));
                    DEBUGLOG(7, "partialDecoding: remaining space in srcBuffer : %i", (int)(iend - ip));
                    /* Finishing in the middle of a literals segment,
                     * due to lack of input.
                     */
                    if (ip+length > iend) {
                        length = (size_t)(iend-ip);
                        cpy = op + length;
                    }
                    /* Finishing in the middle of a literals segment,
                     * due to lack of output space.
                     */
                    if (cpy > oend) {
                        cpy = oend;
                        assert(op<=oend);
                        length = (size_t)(oend-op);
                    }
                } else {
                     /* We must be on the last sequence (or invalid) because of the parsing limitations
                      * so check that we exactly consume the input and don't overrun the output buffer.
                      */
                    if ((ip+length != iend) || (cpy > oend)) {
                        DEBUGLOG(5, "should have been last run of literals")
                        DEBUGLOG(5, "ip(%p) + length(%i) = %p != iend (%p)", ip, (int)length, ip+length, iend);
                        DEBUGLOG(5, "or cpy(%p) > (oend-MFLIMIT)(%p)", cpy, oend-MFLIMIT);
                        DEBUGLOG(5, "after writing %u bytes / %i bytes available", (unsigned)(op-(BYTE*)dst), outputSize);
                        goto _output_error;
                    }
                }
                LZ4_memmove(op, ip, length);  /* supports overlapping memory regions, for in-place decompression scenarios */
                ip += length;
                op += length;
                /* Necessarily EOF when !partialDecoding.
                 * When partialDecoding, it is EOF if we've either
                 * filled the output buffer or
                 * can't proceed with reading an offset for following match.
                 */
                if (!partialDecoding || (cpy == oend) || (ip >= (iend-2))) {
                    break;
                }
            } else {
                LZ4_wildCopy8(op, ip, cpy);   /* can overwrite up to 8 bytes beyond cpy */
                ip += length; op = cpy;
            }

            /* get offset */
            offset = LZ4_readLE16(ip); ip+=2;
            match = op - offset;

            /* get matchlength */
            length = token & ML_MASK;
            DEBUGLOG(7, "blockPos%6u: matchLength token = %u", (unsigned)(op-(BYTE*)dst), (unsigned)length);

    _copy_match:
            if (length == ML_MASK) {
                size_t const addl = read_variable_length(&ip, iend - LASTLITERALS + 1, 0);
                if (addl == rvl_error) { goto _output_error; }
                length += addl;
                if (unlikely((uptrval)(op)+length<(uptrval)op)) goto _output_error;   /* overflow detection */
            }
            length += MINMATCH;

#if LZ4_FAST_DEC_LOOP
        safe_match_copy:
#endif
            if ((checkOffset) && (unlikely(match + dictSize < lowPrefix))) goto _output_error;   /* Error : offset outside buffers */
            /* match starting within external dictionary */
            if ((dict==usingExtDict) && (match < lowPrefix)) {
                assert(dictEnd != NULL);
                if (unlikely(op+length > oend-LASTLITERALS)) {
                    if (partialDecoding) length = MIN(length, (size_t)(oend-op));
                    else goto _output_error;   /* doesn't respect parsing restriction */
                }

                if (length <= (size_t)(lowPrefix-match)) {
                    /* match fits entirely within external dictionary : just copy */
                    LZ4_memmove(op, dictEnd - (lowPrefix-match), length);
                    op += length;
                } else {
                    /* match stretches into both external dictionary and current block */
                    size_t const copySize = (size_t)(lowPrefix - match);
                    size_t const restSize = length - copySize;
                    LZ4_memcpy(op, dictEnd - copySize, copySize);
                    op += copySize;
                    if (restSize > (size_t)(op - lowPrefix)) {  /* overlap copy */
                        BYTE* const endOfMatch = op + restSize;
                        const BYTE* copyFrom = lowPrefix;
                        while (op < endOfMatch) *op++ = *copyFrom++;
                    } else {
                        LZ4_memcpy(op, lowPrefix, restSize);
                        op += restSize;
                }   }
                continue;
            }
            assert(match >= lowPrefix);

            /* copy match within block */
            cpy = op + length;

            /* partialDecoding : may end anywhere within the block */
            assert(op<=oend);
            if (partialDecoding && (cpy > oend-MATCH_SAFEGUARD_DISTANCE)) {
                size_t const mlen = MIN(length, (size_t)(oend-op));
                const BYTE* const matchEnd = match + mlen;
                BYTE* const copyEnd = op + mlen;
                if (matchEnd > op) {   /* overlap copy */
                    while (op < copyEnd) { *op++ = *match++; }
                } else {
                    LZ4_memcpy(op, match, mlen);
                }
                op = copyEnd;
                if (op == oend) { break; }
                continue;
            }

            if (unlikely(offset<8)) {
                LZ4_write32(op, 0);   /* silence msan warning when offset==0 */
                op[0] = match[0];
                op[1] = match[1];
                op[2] = match[2];
                op[3] = match[3];
                match += inc32table[offset];
                LZ4_memcpy(op+4, match, 4);
                match -= dec64table[offset];
            } else {
                LZ4_memcpy(op, match, 8);
                match += 8;
            }
            op += 8;

            if (unlikely(cpy > oend-MATCH_SAFEGUARD_DISTANCE)) {
                BYTE* const oCopyLimit = oend - (WILDCOPYLENGTH-1);
                if (cpy > oend-LASTLITERALS) { goto _output_error; } /* Error : last LASTLITERALS bytes must be literals (uncompressed) */
                if (op < oCopyLimit) {
                    LZ4_wildCopy8(op, match, oCopyLimit);
                    match += oCopyLimit - op;
                    op = oCopyLimit;
                }
                while (op < cpy) { *op++ = *match++; }
            } else {
                LZ4_memcpy(op, match, 8);
                if (length > 16) { LZ4_wildCopy8(op+8, match+8, cpy); }
            }
            op = cpy;   /* wildcopy correction */
        }

        /* end of decoding */
        DEBUGLOG(5, "decoded %i bytes", (int) (((char*)op)-dst));
        return (int) (((char*)op)-dst);     /* Nb of output bytes decoded */

        /* Overflow error detected */
    _output_error:
        return (int) (-(((const char*)ip)-src))-1;
    }
}


/*===== Instantiate the API decoding functions. =====*/

LZ4_FORCE_O2
int LZ4_decompress_safe(const char* source, char* dest, int compressedSize, int maxDecompressedSize)
/* clang-format off */
__CPROVER_requires(compressedSize >= 0 && compressedSize <= 16)
__CPROVER_requires(maxDecompressedSize >= 0 && maxDecompressedSize <= 16)
__CPROVER_requires(__CPROVER_is_fresh(source, (size_t)compressedSize + AVOCADO_SPEC_RVL_N))
__CPROVER_requires(__CPROVER_is_fresh(dest, (size_t)maxDecompressedSize))
__CPROVER_assigns(__CPROVER_object_upto(dest, (size_t)maxDecompressedSize))
__CPROVER_ensures(__CPROVER_return_value <= maxDecompressedSize)
__CPROVER_ensures(maxDecompressedSize == 0 ==>
                  (__CPROVER_return_value == 0 || __CPROVER_return_value == -1))
/* clang-format on */
{
    return LZ4_decompress_generic(source, dest, compressedSize, maxDecompressedSize,
                                  decode_full_block, noDict,
                                  (BYTE*)dest, NULL, 0);
}

LZ4_FORCE_O2
int LZ4_decompress_safe_partial(const char* src, char* dst, int compressedSize, int targetOutputSize, int dstCapacity)
/* clang-format off */
__CPROVER_requires(compressedSize >= 0 && compressedSize <= 16)
__CPROVER_requires(targetOutputSize >= 0 && targetOutputSize <= 16)
__CPROVER_requires(dstCapacity >= 0 && dstCapacity <= 16)
__CPROVER_requires(__CPROVER_is_fresh(src, (size_t)compressedSize + AVOCADO_SPEC_RVL_N))
__CPROVER_requires(__CPROVER_is_fresh(dst, (size_t)16))
__CPROVER_assigns(__CPROVER_object_upto(dst, (size_t)16))
__CPROVER_ensures(__CPROVER_return_value <= MIN(targetOutputSize, __CPROVER_old(dstCapacity)))
__CPROVER_ensures((MIN(targetOutputSize, __CPROVER_old(dstCapacity)) == 0) ==>
                  (__CPROVER_return_value == 0 || __CPROVER_return_value == -1))
/* clang-format on */
{
    dstCapacity = MIN(targetOutputSize, dstCapacity);
    return LZ4_decompress_generic(src, dst, compressedSize, dstCapacity,
                                  partial_decode,
                                  noDict, (BYTE*)dst, NULL, 0);
}

LZ4_FORCE_O2
int LZ4_decompress_fast(const char* source, char* dest, int originalSize)
__CPROVER_requires(originalSize > 0 && originalSize <= AVOCADO_SPEC_DUG_OUT)
__CPROVER_requires(__CPROVER_is_fresh(source, AVOCADO_SPEC_DUG_IN))
__CPROVER_requires(__CPROVER_is_fresh(dest, (size_t)originalSize))
__CPROVER_assigns(__CPROVER_object_upto(dest, (size_t)originalSize))
__CPROVER_ensures(__CPROVER_return_value == -1 ||
    (__CPROVER_return_value >= 1 && __CPROVER_return_value <= AVOCADO_SPEC_DUG_IN))
{
    DEBUGLOG(5, "LZ4_decompress_fast");
    return LZ4_decompress_unsafe_generic(
                (const BYTE*)source, (BYTE*)dest, originalSize,
                0, NULL, 0);
}

/*===== Instantiate a few more decoding cases, used more than once. =====*/

/* dest must lie 64 KB into its object so that (dest - 64 KB) is a valid
 * same-object low-prefix pointer, as LZ4_decompress_generic's contract requires. */
static char *avocado_spec_p64k_base;

/*===== Instantiate a few more decoding cases, used more than once. =====*/

LZ4_FORCE_O2 /* Exported, an obsolete API function. */
int LZ4_decompress_safe_withPrefix64k(const char* source, char* dest, int compressedSize, int maxOutputSize)
__CPROVER_requires(compressedSize >= 0 && compressedSize <= 16)
__CPROVER_requires(maxOutputSize >= 0 && maxOutputSize <= 16)
__CPROVER_requires(__CPROVER_is_fresh(source, (size_t)compressedSize + AVOCADO_SPEC_RVL_N))
__CPROVER_requires(__CPROVER_is_fresh(avocado_spec_p64k_base, (size_t)(64 KB) + (size_t)maxOutputSize))
__CPROVER_requires(dest == avocado_spec_p64k_base + (64 KB))
__CPROVER_assigns(__CPROVER_object_upto(dest, (size_t)maxOutputSize))
__CPROVER_ensures(__CPROVER_return_value <= maxOutputSize)
__CPROVER_ensures(maxOutputSize == 0 ==>
    (__CPROVER_return_value == 0 || __CPROVER_return_value == -1))
{
    return LZ4_decompress_generic(source, dest, compressedSize, maxOutputSize,
                                  decode_full_block, withPrefix64k,
                                  (BYTE*)dest - 64 KB, NULL, 0);
}

#define AVOCADO_SPEC_P64K_PREFIX ((size_t)(64 KB))
#define AVOCADO_SPEC_P64K_CAP ((size_t)16)

/* Base of the fresh object that dest is placed 64 KB into, so that
 * dest - 64 KB is a valid pointer into the same object -- exactly what
 * LZ4_decompress_generic's withPrefix64k lowPrefix precondition demands. */
static char* avocado_spec_dsppfx64k_base;

LZ4_FORCE_O2
static int LZ4_decompress_safe_partial_withPrefix64k(const char* source, char* dest, int compressedSize, int targetOutputSize, int dstCapacity)
__CPROVER_requires(compressedSize >= 0 && compressedSize <= 16)
__CPROVER_requires(targetOutputSize >= 0 && targetOutputSize <= 16)
__CPROVER_requires(dstCapacity >= 0 && dstCapacity <= 16)
__CPROVER_requires(__CPROVER_is_fresh(source, (size_t)compressedSize + AVOCADO_SPEC_RVL_N))
__CPROVER_requires(__CPROVER_is_fresh(avocado_spec_dsppfx64k_base, AVOCADO_SPEC_P64K_PREFIX + AVOCADO_SPEC_P64K_CAP))
__CPROVER_requires(dest == avocado_spec_dsppfx64k_base + AVOCADO_SPEC_P64K_PREFIX)
__CPROVER_assigns(__CPROVER_object_upto(dest, AVOCADO_SPEC_P64K_CAP))
__CPROVER_ensures(__CPROVER_return_value <= MIN(__CPROVER_old(targetOutputSize), __CPROVER_old(dstCapacity)))
__CPROVER_ensures(MIN(__CPROVER_old(targetOutputSize), __CPROVER_old(dstCapacity)) == 0
                  ==> (__CPROVER_return_value == 0 || __CPROVER_return_value == -1))
{
    dstCapacity = MIN(targetOutputSize, dstCapacity);
    return LZ4_decompress_generic(source, dest, compressedSize, dstCapacity,
                                  partial_decode, withPrefix64k,
                                  (BYTE*)dest - 64 KB, NULL, 0);
}

/* Another obsolete API function, paired with the previous one. */
int LZ4_decompress_fast_withPrefix64k(const char* source, char* dest, int originalSize)
__CPROVER_requires(originalSize > 0 && originalSize <= AVOCADO_SPEC_DUG_OUT)
__CPROVER_requires(__CPROVER_is_fresh(source, AVOCADO_SPEC_DUG_IN))
__CPROVER_requires(__CPROVER_is_fresh(dest, (size_t)originalSize))
__CPROVER_assigns(__CPROVER_object_upto(dest, (size_t)originalSize))
__CPROVER_ensures(__CPROVER_return_value == -1 ||
    (__CPROVER_return_value >= 1 && __CPROVER_return_value <= AVOCADO_SPEC_DUG_IN))
{
    return LZ4_decompress_unsafe_generic(
                (const BYTE*)source, (BYTE*)dest, originalSize,
                64 KB, NULL, 0);
}

/* dest must lie prefixSize into its object so that (dest - prefixSize) is a
 * valid same-object low-prefix pointer, as LZ4_decompress_generic requires. */
static char *avocado_spec_smallprefix_base;

LZ4_FORCE_O2
static int LZ4_decompress_safe_withSmallPrefix(const char* source, char* dest, int compressedSize, int maxOutputSize,
                                               size_t prefixSize)
__CPROVER_requires(compressedSize >= 0 && compressedSize <= 16)
__CPROVER_requires(maxOutputSize >= 0 && maxOutputSize <= 16)
__CPROVER_requires(prefixSize <= 16)
__CPROVER_requires(__CPROVER_is_fresh(source, (size_t)compressedSize + AVOCADO_SPEC_RVL_N))
__CPROVER_requires(__CPROVER_is_fresh(avocado_spec_smallprefix_base, prefixSize + (size_t)maxOutputSize + 1))
__CPROVER_requires(dest == avocado_spec_smallprefix_base + prefixSize)
__CPROVER_assigns(__CPROVER_object_upto(dest, (size_t)maxOutputSize))
__CPROVER_ensures(__CPROVER_return_value <= maxOutputSize)
__CPROVER_ensures(maxOutputSize == 0 ==>
    (__CPROVER_return_value == 0 || __CPROVER_return_value == -1))
{
    return LZ4_decompress_generic(source, dest, compressedSize, maxOutputSize,
                                  decode_full_block, noDict,
                                  (BYTE*)dest-prefixSize, NULL, 0);
}

LZ4_FORCE_O2
static int LZ4_decompress_safe_partial_withSmallPrefix(const char* source, char* dest, int compressedSize, int targetOutputSize, int dstCapacity,
                                               size_t prefixSize)
__CPROVER_requires(compressedSize >= 0 && compressedSize <= 16)
__CPROVER_requires(targetOutputSize >= 0 && targetOutputSize <= 16)
__CPROVER_requires(dstCapacity >= 0 && dstCapacity <= 16)
__CPROVER_requires(prefixSize <= 16)
__CPROVER_requires(__CPROVER_is_fresh(source, (size_t)compressedSize + AVOCADO_SPEC_RVL_N))
__CPROVER_requires(__CPROVER_is_fresh(avocado_spec_smallprefix_base, prefixSize + (size_t)dstCapacity + 1))
__CPROVER_requires(dest == avocado_spec_smallprefix_base + prefixSize)
__CPROVER_assigns(__CPROVER_object_upto(dest, (size_t)dstCapacity))
__CPROVER_ensures(__CPROVER_return_value <= targetOutputSize)
__CPROVER_ensures(__CPROVER_return_value <= dstCapacity)
__CPROVER_ensures((targetOutputSize == 0 || dstCapacity == 0) ==>
    (__CPROVER_return_value == 0 || __CPROVER_return_value == -1))
{
    dstCapacity = MIN(targetOutputSize, dstCapacity);
    return LZ4_decompress_generic(source, dest, compressedSize, dstCapacity,
                                  partial_decode, noDict,
                                  (BYTE*)dest-prefixSize, NULL, 0);
}

LZ4_FORCE_O2
int LZ4_decompress_safe_forceExtDict(const char* source, char* dest,
                                     int compressedSize, int maxOutputSize,
                                     const void* dictStart, size_t dictSize)
__CPROVER_requires(compressedSize >= 0 && compressedSize <= 16)
__CPROVER_requires(maxOutputSize >= 0 && maxOutputSize <= 16)
__CPROVER_requires(__CPROVER_is_fresh(source, (size_t)compressedSize + AVOCADO_SPEC_RVL_N))
__CPROVER_requires(__CPROVER_is_fresh(dest, (size_t)maxOutputSize))
__CPROVER_requires(dictSize <= 16)
__CPROVER_requires(dictStart == NULL || __CPROVER_is_fresh(dictStart, dictSize))
__CPROVER_assigns(__CPROVER_object_upto(dest, (size_t)maxOutputSize))
__CPROVER_ensures(__CPROVER_return_value <= maxOutputSize)
__CPROVER_ensures(maxOutputSize == 0 ==>
    (__CPROVER_return_value == 0 || __CPROVER_return_value == -1))
{
    DEBUGLOG(5, "LZ4_decompress_safe_forceExtDict");
    return LZ4_decompress_generic(source, dest, compressedSize, maxOutputSize,
                                  decode_full_block, usingExtDict,
                                  (BYTE*)dest, (const BYTE*)dictStart, dictSize);
}

LZ4_FORCE_O2
int LZ4_decompress_safe_partial_forceExtDict(const char* source, char* dest,
                                     int compressedSize, int targetOutputSize, int dstCapacity,
                                     const void* dictStart, size_t dictSize)
/* clang-format off */
__CPROVER_requires(compressedSize >= 0 && compressedSize <= 16)
__CPROVER_requires(targetOutputSize >= 0 && targetOutputSize <= 16)
__CPROVER_requires(dstCapacity >= 0 && dstCapacity <= 16)
__CPROVER_requires(dictSize <= 16)
__CPROVER_requires(__CPROVER_is_fresh(source, (size_t)compressedSize + AVOCADO_SPEC_RVL_N))
__CPROVER_requires(__CPROVER_is_fresh(dest, (size_t)16))
__CPROVER_requires(dictStart == NULL || __CPROVER_is_fresh(dictStart, dictSize))
__CPROVER_assigns(__CPROVER_object_upto(dest, (size_t)16))
__CPROVER_ensures(__CPROVER_return_value <= MIN(targetOutputSize, __CPROVER_old(dstCapacity)))
__CPROVER_ensures((MIN(targetOutputSize, __CPROVER_old(dstCapacity)) == 0) ==>
                  (__CPROVER_return_value == 0 || __CPROVER_return_value == -1))
/* clang-format on */
{
    dstCapacity = MIN(targetOutputSize, dstCapacity);
    return LZ4_decompress_generic(source, dest, compressedSize, dstCapacity,
                                  partial_decode, usingExtDict,
                                  (BYTE*)dest, (const BYTE*)dictStart, dictSize);
}

LZ4_FORCE_O2
static int LZ4_decompress_fast_extDict(const char* source, char* dest, int originalSize,
                                       const void* dictStart, size_t dictSize)
__CPROVER_requires(originalSize > 0 && originalSize <= AVOCADO_SPEC_DUG_OUT)
__CPROVER_requires(dictStart == NULL)
__CPROVER_requires(dictSize == 0)
__CPROVER_requires(__CPROVER_is_fresh(source, AVOCADO_SPEC_DUG_IN))
__CPROVER_requires(__CPROVER_is_fresh(dest, (size_t)originalSize))
__CPROVER_assigns(__CPROVER_object_upto(dest, (size_t)originalSize))
__CPROVER_ensures(__CPROVER_return_value == -1 ||
    (__CPROVER_return_value >= 1 && __CPROVER_return_value <= AVOCADO_SPEC_DUG_IN))
{
    return LZ4_decompress_unsafe_generic(
                (const BYTE*)source, (BYTE*)dest, originalSize,
                0, (const BYTE*)dictStart, dictSize);
}

/* The "double dictionary" mode, for use with e.g. ring buffers: the first part
 * of the dictionary is passed as prefix, and the second via dictStart + dictSize.
 * These routines are used only once, in LZ4_decompress_*_continue().
 */
/* As with LZ4_decompress_safe_withSmallPrefix, dest must lie prefixSize into its
 * object so that (dest - prefixSize) is a valid same-object low-prefix pointer. */
static char *avocado_spec_doubledict_base;

/* The "double dictionary" mode, for use with e.g. ring buffers: the first part
 * of the dictionary is passed as prefix, and the second via dictStart + dictSize.
 * These routines are used only once, in LZ4_decompress_*_continue().
 */
LZ4_FORCE_INLINE
int LZ4_decompress_safe_doubleDict(const char* source, char* dest, int compressedSize, int maxOutputSize,
                                   size_t prefixSize, const void* dictStart, size_t dictSize)
__CPROVER_requires(compressedSize >= 0 && compressedSize <= 16)
__CPROVER_requires(maxOutputSize >= 0 && maxOutputSize <= 16)
__CPROVER_requires(prefixSize <= 16)
__CPROVER_requires(dictSize <= 16)
__CPROVER_requires(__CPROVER_is_fresh(source, (size_t)compressedSize + AVOCADO_SPEC_RVL_N))
__CPROVER_requires(__CPROVER_is_fresh(avocado_spec_doubledict_base, prefixSize + (size_t)maxOutputSize + 1))
__CPROVER_requires(dest == avocado_spec_doubledict_base + prefixSize)
__CPROVER_requires(dictStart == NULL || __CPROVER_is_fresh(dictStart, dictSize))
__CPROVER_assigns(__CPROVER_object_upto(dest, (size_t)maxOutputSize))
__CPROVER_ensures(__CPROVER_return_value <= maxOutputSize)
__CPROVER_ensures(maxOutputSize == 0 ==>
    (__CPROVER_return_value == 0 || __CPROVER_return_value == -1))
{
    return LZ4_decompress_generic(source, dest, compressedSize, maxOutputSize,
                                  decode_full_block, usingExtDict,
                                  (BYTE*)dest-prefixSize, (const BYTE*)dictStart, dictSize);
}

/*===== streaming decompression functions =====*/

#if !defined(LZ4_STATIC_LINKING_ONLY_DISABLE_MEMORY_ALLOCATION)
LZ4_streamDecode_t* LZ4_createStreamDecode(void)
__CPROVER_assigns()
__CPROVER_ensures(
    __CPROVER_return_value == NULL
    || __CPROVER_is_fresh(__CPROVER_return_value, sizeof(LZ4_streamDecode_t)))
{
    LZ4_STATIC_ASSERT(sizeof(LZ4_streamDecode_t) >= sizeof(LZ4_streamDecode_t_internal));
    return (LZ4_streamDecode_t*) ALLOC_AND_ZERO(sizeof(LZ4_streamDecode_t));
}

int LZ4_freeStreamDecode (LZ4_streamDecode_t* LZ4_stream)
__CPROVER_requires(LZ4_stream == NULL)
__CPROVER_ensures(__CPROVER_return_value == 0)
{
    if (LZ4_stream == NULL) { return 0; }  /* support free on NULL */
    FREEMEM(LZ4_stream);
    return 0;
}
#endif

/*! LZ4_setStreamDecode() :
 *  Use this function to instruct where to find the dictionary.
 *  This function is not necessary if previous data is still available where it was decoded.
 *  Loading a size of 0 is allowed (same effect as no dictionary).
 * @return : 1 if OK, 0 if error
 */
int LZ4_setStreamDecode (LZ4_streamDecode_t* LZ4_streamDecode, const char* dictionary, int dictSize)
__CPROVER_requires(__CPROVER_is_fresh(LZ4_streamDecode, sizeof(LZ4_streamDecode_t)))
__CPROVER_requires(dictSize >= 0 && dictSize <= 16)
__CPROVER_requires(__CPROVER_is_fresh(dictionary, 16))
__CPROVER_assigns(LZ4_streamDecode->internal_donotuse)
__CPROVER_ensures(__CPROVER_return_value == 1)
__CPROVER_ensures(LZ4_streamDecode->internal_donotuse.prefixSize == (size_t)dictSize)
__CPROVER_ensures(LZ4_streamDecode->internal_donotuse.prefixEnd == (const LZ4_byte*)dictionary + dictSize)
__CPROVER_ensures(LZ4_streamDecode->internal_donotuse.externalDict == NULL)
__CPROVER_ensures(LZ4_streamDecode->internal_donotuse.extDictSize == 0)
{
    LZ4_streamDecode_t_internal* lz4sd = &LZ4_streamDecode->internal_donotuse;
    lz4sd->prefixSize = (size_t)dictSize;
    if (dictSize) {
        assert(dictionary != NULL);
        lz4sd->prefixEnd = (const BYTE*) dictionary + dictSize;
    } else {
        lz4sd->prefixEnd = (const BYTE*) dictionary;
    }
    lz4sd->externalDict = NULL;
    lz4sd->extDictSize  = 0;
    return 1;
}

/*! LZ4_decoderRingBufferSize() :
 *  when setting a ring buffer for streaming decompression (optional scenario),
 *  provides the minimum size of this ring buffer
 *  to be compatible with any source respecting maxBlockSize condition.
 *  Note : in a ring buffer scenario,
 *  blocks are presumed decompressed next to each other.
 *  When not enough space remains for next block (remainingSize < maxBlockSize),
 *  decoding resumes from beginning of ring buffer.
 * @return : minimum ring buffer size,
 *           or 0 if there is an error (invalid maxBlockSize).
 */
int LZ4_decoderRingBufferSize(int maxBlockSize)
__CPROVER_assigns()
__CPROVER_ensures(
    (maxBlockSize < 0 ==> __CPROVER_return_value == 0) &&
    (maxBlockSize > LZ4_MAX_INPUT_SIZE ==> __CPROVER_return_value == 0) &&
    (maxBlockSize >= 0 && maxBlockSize < 16 ==>
        __CPROVER_return_value == 65536 + 14 + 16) &&
    (maxBlockSize >= 16 && maxBlockSize <= LZ4_MAX_INPUT_SIZE ==>
        __CPROVER_return_value == 65536 + 14 + maxBlockSize)
)
{
    if (maxBlockSize < 0) return 0;
    if (maxBlockSize > LZ4_MAX_INPUT_SIZE) return 0;
    if (maxBlockSize < 16) maxBlockSize = 16;
    return LZ4_DECODER_RING_BUFFER_SIZE(maxBlockSize);
}

/*
*_continue() :
    These decoding functions allow decompression of multiple blocks in "streaming" mode.
    Previously decoded blocks must still be available at the memory position where they were decoded.
    If it's not possible, save the relevant part of decoded data into a safe buffer,
    and indicate where it stands using LZ4_setStreamDecode()
*/
LZ4_FORCE_O2
int LZ4_decompress_safe_continue (LZ4_streamDecode_t* LZ4_streamDecode, const char* source, char* dest, int compressedSize, int maxOutputSize)
__CPROVER_requires(__CPROVER_is_fresh(LZ4_streamDecode, sizeof(LZ4_streamDecode_t)))
__CPROVER_requires(compressedSize >= 0 && compressedSize <= 16)
__CPROVER_requires(maxOutputSize >= 0 && maxOutputSize <= 16)
__CPROVER_requires(__CPROVER_is_fresh(source, (size_t)compressedSize + AVOCADO_SPEC_RVL_N))
__CPROVER_requires(__CPROVER_is_fresh(dest, (size_t)maxOutputSize))
__CPROVER_requires(LZ4_streamDecode->internal_donotuse.prefixSize == 0)
__CPROVER_requires(LZ4_streamDecode->internal_donotuse.extDictSize == 0)
__CPROVER_assigns(__CPROVER_object_upto(dest, (size_t)maxOutputSize))
__CPROVER_assigns(LZ4_streamDecode->internal_donotuse)
__CPROVER_ensures(__CPROVER_return_value <= maxOutputSize)
__CPROVER_ensures(maxOutputSize == 0 ==>
    (__CPROVER_return_value == 0 || __CPROVER_return_value == -1))
__CPROVER_ensures(__CPROVER_return_value > 0 ==>
    LZ4_streamDecode->internal_donotuse.prefixSize == (size_t)__CPROVER_return_value)
__CPROVER_ensures(__CPROVER_return_value > 0 ==>
    LZ4_streamDecode->internal_donotuse.prefixEnd == (const LZ4_byte*)dest + __CPROVER_return_value)
__CPROVER_ensures(__CPROVER_return_value <= 0 ==>
    LZ4_streamDecode->internal_donotuse.prefixSize == 0)
__CPROVER_ensures(__CPROVER_return_value <= 0 ==>
    LZ4_streamDecode->internal_donotuse.extDictSize == 0)
{
    LZ4_streamDecode_t_internal* lz4sd = &LZ4_streamDecode->internal_donotuse;
    int result;

    if (lz4sd->prefixSize == 0) {
        /* The first call, no dictionary yet. */
        assert(lz4sd->extDictSize == 0);
        result = LZ4_decompress_safe(source, dest, compressedSize, maxOutputSize);
        if (result <= 0) return result;
        lz4sd->prefixSize = (size_t)result;
        lz4sd->prefixEnd = (BYTE*)dest + result;
    } else if (lz4sd->prefixEnd == (BYTE*)dest) {
        /* They're rolling the current segment. */
        if (lz4sd->prefixSize >= 64 KB - 1)
            result = LZ4_decompress_safe_withPrefix64k(source, dest, compressedSize, maxOutputSize);
        else if (lz4sd->extDictSize == 0)
            result = LZ4_decompress_safe_withSmallPrefix(source, dest, compressedSize, maxOutputSize,
                                                         lz4sd->prefixSize);
        else
            result = LZ4_decompress_safe_doubleDict(source, dest, compressedSize, maxOutputSize,
                                                    lz4sd->prefixSize, lz4sd->externalDict, lz4sd->extDictSize);
        if (result <= 0) return result;
        lz4sd->prefixSize += (size_t)result;
        lz4sd->prefixEnd  += result;
    } else {
        /* The buffer wraps around, or they're switching to another buffer. */
        lz4sd->extDictSize = lz4sd->prefixSize;
        lz4sd->externalDict = lz4sd->prefixEnd - lz4sd->extDictSize;
        result = LZ4_decompress_safe_forceExtDict(source, dest, compressedSize, maxOutputSize,
                                                  lz4sd->externalDict, lz4sd->extDictSize);
        if (result <= 0) return result;
        lz4sd->prefixSize = (size_t)result;
        lz4sd->prefixEnd  = (BYTE*)dest + result;
    }

    return result;
}

LZ4_FORCE_O2 int
LZ4_decompress_fast_continue (LZ4_streamDecode_t* LZ4_streamDecode,
                        const char* source, char* dest, int originalSize)
__CPROVER_requires(__CPROVER_is_fresh(LZ4_streamDecode, sizeof(LZ4_streamDecode_t)))
__CPROVER_requires(originalSize > 0 && originalSize <= AVOCADO_SPEC_DUG_OUT)
__CPROVER_requires(__CPROVER_is_fresh(source, AVOCADO_SPEC_DUG_IN))
__CPROVER_requires(__CPROVER_is_fresh(dest, (size_t)originalSize))
__CPROVER_requires(LZ4_streamDecode->internal_donotuse.prefixSize == 0)
__CPROVER_requires(LZ4_streamDecode->internal_donotuse.extDictSize == 0)
__CPROVER_assigns(__CPROVER_object_upto(dest, (size_t)originalSize))
__CPROVER_assigns(LZ4_streamDecode->internal_donotuse)
__CPROVER_ensures(__CPROVER_return_value == -1 ||
    (__CPROVER_return_value >= 1 && __CPROVER_return_value <= AVOCADO_SPEC_DUG_IN))
__CPROVER_ensures(__CPROVER_return_value >= 1 ==>
    LZ4_streamDecode->internal_donotuse.prefixSize == (size_t)originalSize)
__CPROVER_ensures(__CPROVER_return_value >= 1 ==>
    LZ4_streamDecode->internal_donotuse.prefixEnd == (const LZ4_byte*)dest + originalSize)
__CPROVER_ensures(__CPROVER_return_value == -1 ==>
    LZ4_streamDecode->internal_donotuse.prefixSize == 0)
{
    LZ4_streamDecode_t_internal* const lz4sd =
        (assert(LZ4_streamDecode!=NULL), &LZ4_streamDecode->internal_donotuse);
    int result;

    DEBUGLOG(5, "LZ4_decompress_fast_continue (toDecodeSize=%i)", originalSize);
    assert(originalSize >= 0);

    if (lz4sd->prefixSize == 0) {
        DEBUGLOG(5, "first invocation : no prefix nor extDict");
        assert(lz4sd->extDictSize == 0);
        result = LZ4_decompress_fast(source, dest, originalSize);
        if (result <= 0) return result;
        lz4sd->prefixSize = (size_t)originalSize;
        lz4sd->prefixEnd = (BYTE*)dest + originalSize;
    } else if (lz4sd->prefixEnd == (BYTE*)dest) {
        DEBUGLOG(5, "continue using existing prefix");
        result = LZ4_decompress_unsafe_generic(
                        (const BYTE*)source, (BYTE*)dest, originalSize,
                        lz4sd->prefixSize,
                        lz4sd->externalDict, lz4sd->extDictSize);
        if (result <= 0) return result;
        lz4sd->prefixSize += (size_t)originalSize;
        lz4sd->prefixEnd  += originalSize;
    } else {
        DEBUGLOG(5, "prefix becomes extDict");
        lz4sd->extDictSize = lz4sd->prefixSize;
        lz4sd->externalDict = lz4sd->prefixEnd - lz4sd->extDictSize;
        result = LZ4_decompress_fast_extDict(source, dest, originalSize,
                                             lz4sd->externalDict, lz4sd->extDictSize);
        if (result <= 0) return result;
        lz4sd->prefixSize = (size_t)originalSize;
        lz4sd->prefixEnd  = (BYTE*)dest + originalSize;
    }

    return result;
}


/*
Advanced decoding functions :
*_usingDict() :
    These decoding functions work the same as "_continue" ones,
    the dictionary must be explicitly provided within parameters
*/

int LZ4_decompress_safe_usingDict(const char* source, char* dest, int compressedSize, int maxOutputSize, const char* dictStart, int dictSize)
/* clang-format off */
__CPROVER_requires(compressedSize >= 0 && compressedSize <= 16)
__CPROVER_requires(maxOutputSize >= 0 && maxOutputSize <= 16)
__CPROVER_requires(dictSize >= 1 && dictSize <= 16)
__CPROVER_requires(__CPROVER_is_fresh(source, (size_t)compressedSize + AVOCADO_SPEC_RVL_N))
__CPROVER_requires(__CPROVER_is_fresh(dest, (size_t)maxOutputSize))
__CPROVER_requires(__CPROVER_is_fresh(dictStart, (size_t)dictSize))
__CPROVER_assigns(__CPROVER_object_upto(dest, (size_t)maxOutputSize))
__CPROVER_ensures(__CPROVER_return_value <= maxOutputSize)
__CPROVER_ensures(maxOutputSize == 0 ==>
    (__CPROVER_return_value == 0 || __CPROVER_return_value == -1))
/* clang-format on */
{
    if (dictSize==0)
        return LZ4_decompress_safe(source, dest, compressedSize, maxOutputSize);
    if (dictStart+dictSize == dest) {
        if (dictSize >= 64 KB - 1) {
            return LZ4_decompress_safe_withPrefix64k(source, dest, compressedSize, maxOutputSize);
        }
        assert(dictSize >= 0);
        return LZ4_decompress_safe_withSmallPrefix(source, dest, compressedSize, maxOutputSize, (size_t)dictSize);
    }
    assert(dictSize >= 0);
    return LZ4_decompress_safe_forceExtDict(source, dest, compressedSize, maxOutputSize, dictStart, (size_t)dictSize);
}

int LZ4_decompress_safe_partial_usingDict(const char* source, char* dest, int compressedSize, int targetOutputSize, int dstCapacity, const char* dictStart, int dictSize)
__CPROVER_requires(compressedSize >= 0 && compressedSize <= 16)
__CPROVER_requires(targetOutputSize >= 0 && targetOutputSize <= 16)
__CPROVER_requires(dstCapacity >= 0 && dstCapacity <= 16)
__CPROVER_requires(dictSize >= 0 && dictSize <= 16)
__CPROVER_requires(__CPROVER_is_fresh(source, (size_t)compressedSize + AVOCADO_SPEC_RVL_N))
__CPROVER_requires(__CPROVER_is_fresh(dest, (size_t)16))
__CPROVER_requires(dictSize == 0 || __CPROVER_is_fresh(dictStart, (size_t)dictSize))
__CPROVER_assigns(__CPROVER_object_upto(dest, (size_t)16))
__CPROVER_ensures(__CPROVER_return_value <= MIN(targetOutputSize, dstCapacity))
__CPROVER_ensures((MIN(targetOutputSize, dstCapacity) == 0) ==>
                  (__CPROVER_return_value == 0 || __CPROVER_return_value == -1))
{
    if (dictSize==0)
        return LZ4_decompress_safe_partial(source, dest, compressedSize, targetOutputSize, dstCapacity);
    if (dictStart+dictSize == dest) {
        if (dictSize >= 64 KB - 1) {
            return LZ4_decompress_safe_partial_withPrefix64k(source, dest, compressedSize, targetOutputSize, dstCapacity);
        }
        assert(dictSize >= 0);
        return LZ4_decompress_safe_partial_withSmallPrefix(source, dest, compressedSize, targetOutputSize, dstCapacity, (size_t)dictSize);
    }
    assert(dictSize >= 0);
    return LZ4_decompress_safe_partial_forceExtDict(source, dest, compressedSize, targetOutputSize, dstCapacity, dictStart, (size_t)dictSize);
}

int LZ4_decompress_fast_usingDict(const char* source, char* dest, int originalSize, const char* dictStart, int dictSize)
__CPROVER_requires(originalSize > 0 && originalSize <= AVOCADO_SPEC_DUG_OUT)
__CPROVER_requires(dictSize == 0)
__CPROVER_requires(__CPROVER_is_fresh(source, AVOCADO_SPEC_DUG_IN))
__CPROVER_requires(__CPROVER_is_fresh(dest, (size_t)originalSize))
__CPROVER_assigns(__CPROVER_object_upto(dest, (size_t)originalSize))
__CPROVER_ensures(__CPROVER_return_value == -1 ||
    (__CPROVER_return_value >= 1 && __CPROVER_return_value <= AVOCADO_SPEC_DUG_IN))
{
    if (dictSize==0 || dictStart+dictSize == dest)
        return LZ4_decompress_unsafe_generic(
                        (const BYTE*)source, (BYTE*)dest, originalSize,
                        (size_t)dictSize, NULL, 0);
    assert(dictSize >= 0);
    return LZ4_decompress_fast_extDict(source, dest, originalSize, dictStart, (size_t)dictSize);
}


/*=*************************************************
*  Obsolete Functions
***************************************************/
/* obsolete compression functions */
int LZ4_compress_limitedOutput(const char* source, char* dest, int inputSize, int maxOutputSize)
__CPROVER_requires(inputSize == 0 || inputSize == 11 || inputSize == 12)
__CPROVER_requires(__CPROVER_is_fresh(dest, AVOCADO_SPEC_CGV_DSTCAP))
__CPROVER_requires(inputSize != 0 ==> __CPROVER_is_fresh(source, (size_t)inputSize))
__CPROVER_requires(maxOutputSize >= 0)
__CPROVER_requires(maxOutputSize <= AVOCADO_SPEC_CGV_MAXOUT
                || (maxOutputSize >= inputSize + 16 && maxOutputSize <= 64))
__CPROVER_assigns(__CPROVER_object_upto(dest, (size_t)inputSize + 1))
/* return value */
__CPROVER_ensures(inputSize == 0 && maxOutputSize == 0 ==> __CPROVER_return_value == 0)
__CPROVER_ensures(inputSize == 0 && maxOutputSize >= 1 ==> __CPROVER_return_value == 1)
__CPROVER_ensures(inputSize == 11 && maxOutputSize < 12 ==> __CPROVER_return_value == 0)
__CPROVER_ensures(inputSize == 11 && maxOutputSize >= 12 ==> __CPROVER_return_value == 12)
__CPROVER_ensures(inputSize == 12 && maxOutputSize < 13 ==> __CPROVER_return_value == 0)
__CPROVER_ensures(inputSize == 12 && maxOutputSize >= 13 ==> __CPROVER_return_value == 13)
/* destination bytes on a successful compression */
__CPROVER_ensures(inputSize == 0 && maxOutputSize >= 1 ==> ((BYTE*)dest)[0] == 0)
__CPROVER_ensures(inputSize == 11 && maxOutputSize >= 12
    ==> ((BYTE*)dest)[0] == (BYTE)((unsigned)11 << ML_BITS))
__CPROVER_ensures(inputSize == 12 && maxOutputSize >= 13
    ==> ((BYTE*)dest)[0] == (BYTE)((unsigned)12 << ML_BITS))
__CPROVER_ensures(inputSize == 11 && maxOutputSize >= 12
    ==> __CPROVER_forall { int avocado_spec_i;
            (avocado_spec_i >= 0 && avocado_spec_i < 11)
                ==> ((BYTE*)dest)[avocado_spec_i + 1]
                    == ((const BYTE*)source)[avocado_spec_i] })
__CPROVER_ensures(inputSize == 12 && maxOutputSize >= 13
    ==> __CPROVER_forall { int avocado_spec_j;
            (avocado_spec_j >= 0 && avocado_spec_j < 12)
                ==> ((BYTE*)dest)[avocado_spec_j + 1]
                    == ((const BYTE*)source)[avocado_spec_j] })
{
    return LZ4_compress_default(source, dest, inputSize, maxOutputSize);
}
int LZ4_compress(const char* src, char* dest, int srcSize)
__CPROVER_requires(srcSize == 0 || srcSize == 11 || srcSize == 12)
__CPROVER_requires(__CPROVER_is_fresh(dest, AVOCADO_SPEC_CGV_DSTCAP))
__CPROVER_requires(srcSize != 0 ==> __CPROVER_is_fresh(src, (size_t)srcSize))
__CPROVER_assigns(__CPROVER_object_upto(dest, (size_t)srcSize + 1))
/* return value: compressBound(srcSize) always guarantees success here */
__CPROVER_ensures(srcSize == 0 ==> __CPROVER_return_value == 1)
__CPROVER_ensures(srcSize == 11 ==> __CPROVER_return_value == 12)
__CPROVER_ensures(srcSize == 12 ==> __CPROVER_return_value == 13)
/* destination bytes on a successful compression */
__CPROVER_ensures(srcSize == 0 ==> ((BYTE*)dest)[0] == 0)
__CPROVER_ensures(srcSize == 11 ==> ((BYTE*)dest)[0] == (BYTE)((unsigned)11 << ML_BITS))
__CPROVER_ensures(srcSize == 12 ==> ((BYTE*)dest)[0] == (BYTE)((unsigned)12 << ML_BITS))
__CPROVER_ensures(srcSize == 11
    ==> __CPROVER_forall { int avocado_spec_i;
            (avocado_spec_i >= 0 && avocado_spec_i < 11)
                ==> ((BYTE*)dest)[avocado_spec_i + 1]
                    == ((const BYTE*)src)[avocado_spec_i] })
__CPROVER_ensures(srcSize == 12
    ==> __CPROVER_forall { int avocado_spec_j;
            (avocado_spec_j >= 0 && avocado_spec_j < 12)
                ==> ((BYTE*)dest)[avocado_spec_j + 1]
                    == ((const BYTE*)src)[avocado_spec_j] })
{
    return LZ4_compress_default(src, dest, srcSize, LZ4_compressBound(srcSize));
}
int LZ4_compress_limitedOutput_withState (void* state, const char* src, char* dst, int srcSize, int dstSize)
/* Single-call wrapper around LZ4_compress_fast_extState with acceleration==1.
 * Mirror that callee's contract: srcSize -> inputSize, dstSize -> maxOutputSize. */
__CPROVER_requires(srcSize == 0 || srcSize == 11 || srcSize == 12)
__CPROVER_requires(__CPROVER_is_fresh(state, sizeof(LZ4_stream_t)))
__CPROVER_requires(AVOCADO_SPEC_FEXT_ALIGNED(state))
__CPROVER_requires(__CPROVER_is_fresh(dst, AVOCADO_SPEC_CGV_DSTCAP))
__CPROVER_requires(srcSize != 0 ==> __CPROVER_is_fresh(src, (size_t)srcSize))
__CPROVER_requires(dstSize >= 0)
__CPROVER_requires(dstSize <= AVOCADO_SPEC_CGV_MAXOUT
                || (dstSize >= srcSize + 16 && dstSize <= 64))
__CPROVER_assigns(__CPROVER_object_upto(state, sizeof(LZ4_stream_t_internal)))
__CPROVER_assigns(__CPROVER_object_upto(dst, (size_t)srcSize + 1))
/* return value */
__CPROVER_ensures(srcSize == 0 && dstSize == 0 ==> __CPROVER_return_value == 0)
__CPROVER_ensures(srcSize == 0 && dstSize >= 1 ==> __CPROVER_return_value == 1)
__CPROVER_ensures(srcSize == 11 && dstSize < 12 ==> __CPROVER_return_value == 0)
__CPROVER_ensures(srcSize == 11 && dstSize >= 12 ==> __CPROVER_return_value == 12)
__CPROVER_ensures(srcSize == 12 && dstSize < 13 ==> __CPROVER_return_value == 0)
__CPROVER_ensures(srcSize == 12 && dstSize >= 13 ==> __CPROVER_return_value == 13)
/* destination bytes on a successful compression */
__CPROVER_ensures(srcSize == 0 && dstSize >= 1 ==> ((BYTE*)dst)[0] == 0)
__CPROVER_ensures(srcSize == 11 && dstSize >= 12
    ==> ((BYTE*)dst)[0] == (BYTE)((unsigned)11 << ML_BITS))
__CPROVER_ensures(srcSize == 12 && dstSize >= 13
    ==> ((BYTE*)dst)[0] == (BYTE)((unsigned)12 << ML_BITS))
__CPROVER_ensures(srcSize == 11 && dstSize >= 12
    ==> __CPROVER_forall { int avocado_spec_i;
            (avocado_spec_i >= 0 && avocado_spec_i < 11)
                ==> ((BYTE*)dst)[avocado_spec_i + 1]
                    == ((const BYTE*)src)[avocado_spec_i] })
__CPROVER_ensures(srcSize == 12 && dstSize >= 13
    ==> __CPROVER_forall { int avocado_spec_j;
            (avocado_spec_j >= 0 && avocado_spec_j < 12)
                ==> ((BYTE*)dst)[avocado_spec_j + 1]
                    == ((const BYTE*)src)[avocado_spec_j] })
/* stream state after a forward compression (table set to byU16) */
__CPROVER_ensures((srcSize == 11 || srcSize == 12)
    ==> AVOCADO_SPEC_FEXT_CTX->tableType == (U32)byU16)
__CPROVER_ensures((srcSize == 11 || srcSize == 12)
    ==> AVOCADO_SPEC_FEXT_CTX->currentOffset == (U32)srcSize)
__CPROVER_ensures((srcSize == 11 || srcSize == 12)
    ==> AVOCADO_SPEC_FEXT_CTX->dictSize == (U32)srcSize)
{
    return LZ4_compress_fast_extState(state, src, dst, srcSize, dstSize, 1);
}
int LZ4_compress_withState (void* state, const char* src, char* dst, int srcSize)
/* Single-call wrapper around LZ4_compress_fast_extState with acceleration==1 and
 * maxOutputSize == LZ4_compressBound(srcSize), which always guarantees success.
 * Mirror that callee's contract specialized to the success case. */
__CPROVER_requires(srcSize == 0 || srcSize == 11 || srcSize == 12)
__CPROVER_requires(__CPROVER_is_fresh(state, sizeof(LZ4_stream_t)))
__CPROVER_requires(AVOCADO_SPEC_FEXT_ALIGNED(state))
__CPROVER_requires(__CPROVER_is_fresh(dst, AVOCADO_SPEC_CGV_DSTCAP))
__CPROVER_requires(srcSize != 0 ==> __CPROVER_is_fresh(src, (size_t)srcSize))
__CPROVER_assigns(__CPROVER_object_upto(state, sizeof(LZ4_stream_t_internal)))
__CPROVER_assigns(__CPROVER_object_upto(dst, (size_t)srcSize + 1))
/* return value: compressBound(srcSize) always guarantees success here */
__CPROVER_ensures(srcSize == 0 ==> __CPROVER_return_value == 1)
__CPROVER_ensures(srcSize == 11 ==> __CPROVER_return_value == 12)
__CPROVER_ensures(srcSize == 12 ==> __CPROVER_return_value == 13)
/* destination bytes on a successful compression */
__CPROVER_ensures(srcSize == 0 ==> ((BYTE*)dst)[0] == 0)
__CPROVER_ensures(srcSize == 11 ==> ((BYTE*)dst)[0] == (BYTE)((unsigned)11 << ML_BITS))
__CPROVER_ensures(srcSize == 12 ==> ((BYTE*)dst)[0] == (BYTE)((unsigned)12 << ML_BITS))
__CPROVER_ensures(srcSize == 11
    ==> __CPROVER_forall { int avocado_spec_i;
            (avocado_spec_i >= 0 && avocado_spec_i < 11)
                ==> ((BYTE*)dst)[avocado_spec_i + 1]
                    == ((const BYTE*)src)[avocado_spec_i] })
__CPROVER_ensures(srcSize == 12
    ==> __CPROVER_forall { int avocado_spec_j;
            (avocado_spec_j >= 0 && avocado_spec_j < 12)
                ==> ((BYTE*)dst)[avocado_spec_j + 1]
                    == ((const BYTE*)src)[avocado_spec_j] })
/* stream state after a forward compression (table set to byU16) */
__CPROVER_ensures((srcSize == 11 || srcSize == 12)
    ==> AVOCADO_SPEC_FEXT_CTX->tableType == (U32)byU16)
__CPROVER_ensures((srcSize == 11 || srcSize == 12)
    ==> AVOCADO_SPEC_FEXT_CTX->currentOffset == (U32)srcSize)
__CPROVER_ensures((srcSize == 11 || srcSize == 12)
    ==> AVOCADO_SPEC_FEXT_CTX->dictSize == (U32)srcSize)
{
    return LZ4_compress_fast_extState(state, src, dst, srcSize, LZ4_compressBound(srcSize), 1);
}
int LZ4_compress_limitedOutput_continue (LZ4_stream_t* LZ4_stream, const char* src, char* dst, int srcSize, int dstCapacity)
__CPROVER_requires(__CPROVER_is_fresh(LZ4_stream, sizeof(LZ4_stream_t)))
__CPROVER_requires(srcSize == 0 || srcSize == 11 || srcSize == 12)
__CPROVER_requires(dstCapacity >= 0 && dstCapacity <= AVOCADO_SPEC_CGV_MAXOUT)
__CPROVER_requires(__CPROVER_is_fresh(dst, AVOCADO_SPEC_CGV_DSTCAP))
__CPROVER_requires(srcSize != 0 ==> __CPROVER_is_fresh(src, (size_t)srcSize))
__CPROVER_requires(LZ4_stream->internal_donotuse.dictSize <= (U32)(64 KB))
__CPROVER_requires(LZ4_stream->internal_donotuse.dictionary == NULL)
__CPROVER_requires(LZ4_stream->internal_donotuse.dictCtx == NULL)
__CPROVER_requires(LZ4_stream->internal_donotuse.currentOffset == 0)
__CPROVER_assigns(__CPROVER_object_whole(LZ4_stream))
__CPROVER_assigns(__CPROVER_object_upto(dst, (size_t)srcSize + 1))
{
    return LZ4_compress_fast_continue(LZ4_stream, src, dst, srcSize, dstCapacity, 1);
}
int LZ4_compress_continue (LZ4_stream_t* LZ4_stream, const char* source, char* dest, int inputSize)
/* The callee LZ4_compress_fast_continue requires inputSize in {0,11,12} AND
 * maxOutputSize <= AVOCADO_SPEC_CGV_MAXOUT (20). Here maxOutputSize is
 * LZ4_compressBound(inputSize) = inputSize + inputSize/255 + 16, which is 16
 * for 0 but 27/28 for 11/12 -- exceeding 20. inputSize == 0 is the only value
 * satisfying both callee preconditions. */
__CPROVER_requires(__CPROVER_is_fresh(LZ4_stream, sizeof(LZ4_stream_t)))
__CPROVER_requires(inputSize == 0)
__CPROVER_requires(__CPROVER_is_fresh(dest, AVOCADO_SPEC_CGV_DSTCAP))
__CPROVER_requires(inputSize != 0 ==> __CPROVER_is_fresh(source, (size_t)inputSize))
__CPROVER_requires(LZ4_stream->internal_donotuse.dictSize <= (U32)(64 KB))
__CPROVER_requires(LZ4_stream->internal_donotuse.dictionary == NULL)
__CPROVER_requires(LZ4_stream->internal_donotuse.dictCtx == NULL)
__CPROVER_requires(LZ4_stream->internal_donotuse.currentOffset == 0)
__CPROVER_assigns(__CPROVER_object_whole(LZ4_stream))
__CPROVER_assigns(__CPROVER_object_upto(dest, (size_t)inputSize + 1))
{
    return LZ4_compress_fast_continue(LZ4_stream, source, dest, inputSize, LZ4_compressBound(inputSize), 1);
}

/*
These decompression functions are deprecated and should no longer be used.
They are only provided here for compatibility with older user programs.
- LZ4_uncompress is totally equivalent to LZ4_decompress_fast
- LZ4_uncompress_unknownOutputSize is totally equivalent to LZ4_decompress_safe
*/
int LZ4_uncompress (const char* source, char* dest, int outputSize)
__CPROVER_requires(outputSize > 0 && outputSize <= AVOCADO_SPEC_DUG_OUT)
__CPROVER_requires(__CPROVER_is_fresh(source, AVOCADO_SPEC_DUG_IN))
__CPROVER_requires(__CPROVER_is_fresh(dest, (size_t)outputSize))
__CPROVER_assigns(__CPROVER_object_upto(dest, (size_t)outputSize))
__CPROVER_ensures(__CPROVER_return_value == -1 ||
    (__CPROVER_return_value >= 1 && __CPROVER_return_value <= AVOCADO_SPEC_DUG_IN))
{
    return LZ4_decompress_fast(source, dest, outputSize);
}
int LZ4_uncompress_unknownOutputSize (const char* source, char* dest, int isize, int maxOutputSize)
/* clang-format off */
__CPROVER_requires(isize >= 0 && isize <= 16)
__CPROVER_requires(maxOutputSize >= 0 && maxOutputSize <= 16)
__CPROVER_requires(__CPROVER_is_fresh(source, (size_t)isize + AVOCADO_SPEC_RVL_N))
__CPROVER_requires(__CPROVER_is_fresh(dest, (size_t)maxOutputSize))
__CPROVER_assigns(__CPROVER_object_upto(dest, (size_t)maxOutputSize))
__CPROVER_ensures(__CPROVER_return_value <= maxOutputSize)
__CPROVER_ensures(maxOutputSize == 0 ==>
                  (__CPROVER_return_value == 0 || __CPROVER_return_value == -1))
/* clang-format on */
{
    return LZ4_decompress_safe(source, dest, isize, maxOutputSize);
}

/* Obsolete Streaming functions */

int LZ4_sizeofStreamState(void)
__CPROVER_ensures(__CPROVER_return_value == (int)sizeof(LZ4_stream_t))
{ return sizeof(LZ4_stream_t); }

int LZ4_resetStreamState(void* state, char* inputBuffer)
__CPROVER_requires(__CPROVER_is_fresh(state, sizeof(LZ4_stream_t)))
__CPROVER_assigns(__CPROVER_object_upto(state, sizeof(LZ4_stream_t_internal)))
__CPROVER_ensures(__CPROVER_return_value == 0)
{
    (void)inputBuffer;
    LZ4_resetStream((LZ4_stream_t*)state);
    return 0;
}

#if !defined(LZ4_STATIC_LINKING_ONLY_DISABLE_MEMORY_ALLOCATION)
void* LZ4_create (char* inputBuffer)
__CPROVER_assigns()
__CPROVER_ensures(
    __CPROVER_return_value == NULL
    || __CPROVER_is_fresh(__CPROVER_return_value, sizeof(LZ4_stream_t)))
{
    (void)inputBuffer;
    return LZ4_createStream();
}
#endif

char* LZ4_slideInputBuffer (void* state)
__CPROVER_requires(__CPROVER_is_fresh(state, sizeof(LZ4_stream_t)))
__CPROVER_assigns()
__CPROVER_ensures(__CPROVER_return_value == (char *)(uptrval)((LZ4_stream_t*)state)->internal_donotuse.dictionary)
{
    /* avoid const char * -> char * conversion warning */
    return (char *)(uptrval)((LZ4_stream_t*)state)->internal_donotuse.dictionary;
}

#endif   /* LZ4_COMMONDEFS_ONLY */
