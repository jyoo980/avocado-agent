#include <math.h>

#include "lib.h"

void gaussian_kernel(float *dest, int size, float radius)
    __CPROVER_requires(size == 4)
    __CPROVER_requires(radius > 1.0f && radius < 1000.0f)
    __CPROVER_requires(__CPROVER_is_fresh(dest, (size + 1) * sizeof(float)))
    __CPROVER_assigns(__CPROVER_object_whole(dest))
{
    float *k;
    float rs, s2, sum;
    float sigma = 1.6f;
    float tetha = 2.25f;
    int r, hsize = size / 2;
    s2 = 1.0f / expf(sigma * sigma * tetha);
    rs = sigma / radius;
    k = dest;
    sum = 0.0f;
    for (r = -hsize; r <= hsize; r++) {
        float x = r * rs;
        float v = (1.0f / expf(x * x)) - s2;
        v = (((v) > (0)) ? (v) : (0));
        *k = v;
        sum += v;
        k++;
    }
    if (sum > 0.0f) {
        float isum = 1.0f / sum;
        for (r = 0; r < size; r++)
            dest[r] *= isum;
    }
}
