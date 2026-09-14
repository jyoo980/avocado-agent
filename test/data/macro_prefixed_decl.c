/* Function signatures prefixed with specifier-like macros, as in xxhash.c / lz4.c.
 * Tree-sitter cannot know these macros expand to specifiers, so its error recovery may attach
 * the wrong identifier as the function name. */
#include <stddef.h>

#if defined(__GNUC__)
#  define API_MACRO static __inline __attribute__((unused))
#elif defined(_MSC_VER)
#  define API_MACRO static __inline
#else
#  define API_MACRO /* do nothing */
#endif

#if defined(__GNUC__)
#  define FORCE_INLINE_MACRO static inline __attribute__((always_inline))
#else
#  define FORCE_INLINE_MACRO static
#endif

typedef int err_t;
typedef struct { int total; } state_t;

static int helper(int x) { return x + 1; }

API_MACRO err_t update (state_t* s, const void* input, size_t len)
{
    (void)input;
    s->total += (int)len;
    return helper((int)len);
}

API_MACRO err_t update64 (state_t* s, size_t len)
{
    return update(s, NULL, len);
}

FORCE_INLINE_MACRO int
decompress_generic(const char* src, int size)
{
    if (src == NULL) { return -1; }
    return helper(size);
}

char *first_byte(char *buf)
{
    return buf;
}
