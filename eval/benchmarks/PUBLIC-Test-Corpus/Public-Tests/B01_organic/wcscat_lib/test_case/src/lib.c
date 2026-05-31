#include <stddef.h>

#include "lib.h"

int wcscat(wchar_t *dst, size_t numElem, const wchar_t *src)
    __CPROVER_requires(numElem >= 1 && numElem <= 4)
    __CPROVER_requires(__CPROVER_is_fresh(dst, numElem * sizeof(wchar_t)))
    __CPROVER_requires(__CPROVER_is_fresh(src, numElem * sizeof(wchar_t)))
    __CPROVER_assigns(__CPROVER_object_whole(dst))
    __CPROVER_ensures(__CPROVER_return_value == 0 ||
                      __CPROVER_return_value == 34)
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
