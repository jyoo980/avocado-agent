#include "lib.h"

void rgb_to_hsv(float *dest, const float *src)
    __CPROVER_requires(__CPROVER_is_fresh(dest, sizeof(float) * 3) &&
                       __CPROVER_is_fresh(src, sizeof(float) * 3))
    __CPROVER_assigns(dest[0], dest[1], dest[2])
{
    float r = src[0];
    float g = src[1];
    float b = src[2];
    float h = 0;
    float s = 0;
    float v = 0;
    float min = r;
    float max = r;
    float delta;
    min = (((min) < (g)) ? (min) : (g));
    min = (((min) < (b)) ? (min) : (b));
    max = (((max) > (g)) ? (max) : (g));
    max = (((max) > (b)) ? (max) : (b));
    delta = max - min;
    v = max;
    if (delta == 0 || max == 0) {
        dest[0] = h;
        dest[1] = s;
        dest[2] = v;
        return;
    }
    s = delta / max;
    if (r == max)
        h = (g - b) / delta;
    else if (g == max)
        h = 2 + (b - r) / delta;
    else
        h = 4 + (r - g) / delta;
    h *= 60;
    if (h < 0)
        h += 360;
    dest[0] = h;
    dest[1] = s;
    dest[2] = v;
}

#ifdef CBMC_HARNESS
void rgb_to_hsv_harness(void) {
    float *dest;
    const float *src;
    rgb_to_hsv(dest, src);
}
#endif
