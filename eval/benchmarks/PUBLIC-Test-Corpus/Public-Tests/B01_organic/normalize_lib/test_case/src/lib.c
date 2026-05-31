#include <math.h>
#include <string.h>

#include "lib.h"

void normalize(float *dest, const float *src, int size)
    /* size is bounded so the loops unwind fully; both buffers are allocated at
       the maximum size to avoid spurious bounds failures from symbolic-size
       is_fresh under unwinding. */
    __CPROVER_requires(size >= 0 && size <= 16 &&
                       __CPROVER_is_fresh(dest, sizeof(float) * 16) &&
                       __CPROVER_is_fresh(src, sizeof(float) * 16))
    __CPROVER_assigns(__CPROVER_object_whole(dest))
{
    float sum = 0.0f;
    int i;
    for (i = 0; i < size; i++)
        sum += src[i] * src[i];
    if (sum > 0.0f) {
        sum = 1.0f / sqrtf(sum);
        for (i = 0; i < size; i++)
            dest[i] = src[i] * sum;
    } else if (dest != src) {
        memset(dest, 0, size * sizeof(float));
    }
}

#ifdef CBMC_HARNESS
void normalize_harness(void) {
    float *dest;
    const float *src;
    int size;
    normalize(dest, src, size);
}
#endif
