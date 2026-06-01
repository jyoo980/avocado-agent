#include <math.h>

#include "lib.h"

void tfm(float *dest, const float *src, int count)
__CPROVER_requires(count >= 0 && count <= 4)
__CPROVER_requires(__CPROVER_is_fresh(dest, sizeof(float) * 8))
__CPROVER_requires(__CPROVER_is_fresh(src, sizeof(float) * 12))
__CPROVER_assigns(__CPROVER_object_whole(dest))
{
    int i;
    for (i = 0; i < count; i++) {
        if (src[0] < src[1]) {
            float dx2 = src[0];
            float dy2 = src[1];
            float dxy = src[2];
            float sqd = (dy2 * dy2) - (2.0f * dx2 * dy2) + (dx2 * dx2) +
                        (4.0f * dxy * dxy);
            float lambda =
                0.5f * (dy2 + dx2 + sqrtf((((0) > (sqd)) ? (0) : (sqd))));
            dest[0] = dx2 - lambda;
            dest[1] = dxy;
        } else {
            float dy2 = src[0];
            float dx2 = src[1];
            float dxy = src[2];
            float sqd = (dy2 * dy2) - (2.0f * dx2 * dy2) + (dx2 * dx2) +
                        (4.0f * dxy * dxy);
            float lambda =
                0.5f * (dy2 + dx2 + sqrtf((((0) > (sqd)) ? (0) : (sqd))));
            dest[0] = dxy;
            dest[1] = dx2 - lambda;
        }
        src += 3;
        dest += 2;
    }
}
