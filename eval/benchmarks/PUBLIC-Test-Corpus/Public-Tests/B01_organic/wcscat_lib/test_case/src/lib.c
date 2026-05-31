#include <stddef.h>

#include "lib.h"

int wcscat(wchar_t *dst, size_t numElem, const wchar_t *src)
    /* numElem is bounded so the scan/copy loops unwind fully; both buffers are
       allocated at the fixed maximum so their object sizes stay constant as the
       pointers advance. Each loop is bounded by dst+numElem, so at most numElem
       elements are touched. */
    __CPROVER_requires(numElem <= 8 &&
                       __CPROVER_is_fresh(dst, sizeof(wchar_t) * 8) &&
                       __CPROVER_is_fresh(src, sizeof(wchar_t) * 8))
    __CPROVER_assigns(__CPROVER_object_whole(dst))
{
    wchar_t *ptr = dst;
    if (!dst || numElem == 0)
        return 22;
    if (!src) {
        dst[0] = 0;
        return 22;
    }
    while (ptr < dst + numElem && *ptr != 0)
        ptr++;
    while (ptr < dst + numElem) {
        if ((*ptr++ = *src++) == 0)
            return 0;
    }
    dst[0] = 0;
    return 34;
}

#ifdef CBMC_HARNESS
void wcscat_harness(void) {
    wchar_t *dst;
    size_t numElem;
    const wchar_t *src;
    wcscat(dst, numElem, src);
}
#endif
