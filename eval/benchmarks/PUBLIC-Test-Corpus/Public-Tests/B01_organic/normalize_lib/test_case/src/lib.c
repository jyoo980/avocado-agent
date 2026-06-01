#include <math.h>
#include <string.h>

#include "lib.h"

void normalize(float *dest, const float *src, int size)
__CPROVER_requires(size >= 0 && size <= 4)
__CPROVER_requires(__CPROVER_is_fresh(src, (size_t)size * sizeof(float)))
__CPROVER_requires(__CPROVER_is_fresh(dest, (size_t)size * sizeof(float)))
__CPROVER_assigns(__CPROVER_object_upto(dest, (size_t)size * sizeof(float)))
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
