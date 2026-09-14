/* The same function defined in alternative preprocessor branches, as LZ4_read32 / XXH_read32. */
#include <string.h>
typedef unsigned int U32;

#if defined(FORCE_DIRECT)
static U32 read32(const void* p) { return *(const U32*)p; }
#elif defined(FORCE_PACKED)
static U32 read32(const void* p) { return ((const U32*)p)[0]; }
#else
static U32 read32(const void* p)
{
    U32 v;
    memcpy(&v, p, sizeof(v));
    return v;
}
#endif

static U32 hash(const void* p)
{
    return read32(p) * 2654435761U;
}
