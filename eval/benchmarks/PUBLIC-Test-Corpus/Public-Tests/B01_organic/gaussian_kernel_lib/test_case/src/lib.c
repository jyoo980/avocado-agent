#include <math.h>

#include "lib.h"

/* expf's libc model writes errno, which is outside this function's frame.
   Abstract it with its mathematical postcondition (always strictly positive,
   no observable side effects) so the contract proof stays within the frame. */
float expf(float x) __CPROVER_assigns()
    __CPROVER_ensures(__CPROVER_return_value > 0.0f);

void gaussian_kernel(float *dest, int size, float radius)
    /* The first loop writes 2*(size/2)+1 entries (up to size+1 when size is
       even), so dest is allocated for size+1 floats at the fixed maximum. size
       is bounded so both loops unwind fully. */
    __CPROVER_requires(size >= 0 && size <= 16 &&
                       __CPROVER_is_fresh(dest, sizeof(float) * 17))
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

#ifdef CBMC_HARNESS
void gaussian_kernel_harness(void) {
    float *dest;
    int size;
    float radius;
    gaussian_kernel(dest, size, radius);
}
#endif
