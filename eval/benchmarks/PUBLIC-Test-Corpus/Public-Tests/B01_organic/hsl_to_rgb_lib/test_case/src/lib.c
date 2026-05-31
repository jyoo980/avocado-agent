#include <math.h>

#include "lib.h"

void hsl_to_rgb(float *dest, const float *src)
    __CPROVER_requires(__CPROVER_is_fresh(dest, 3 * sizeof(float)))
    __CPROVER_requires(__CPROVER_is_fresh(src, 3 * sizeof(float)))
    __CPROVER_assigns(dest[0], dest[1], dest[2])
{
    float h = src[0];
    float s = src[1];
    float l = src[2];
    float c, m, x;
    if (s == 0) {
        dest[0] = l;
        dest[1] = l;
        dest[2] = l;
        return;
    }
    c = (1.0f - fabsf(2.0f * l - 1.0f)) * s;
    m = 1.0f * (l - 0.5f * c);
    x = c * (1.0f - fabsf(fmodf(h / 60.0f, 2) - 1.0f));
    if (h >= 0.0f && h < 60.0f) {
        dest[0] = c + m;
        dest[1] = x + m;
        dest[2] = m;
    } else if (h >= 60.0f && h < 120.0f) {
        dest[0] = x + m;
        dest[1] = c + m;
        dest[2] = m;
    } else if (h < 120.0f && h < 180.0f) {
        dest[0] = m;
        dest[1] = c + m;
        dest[2] = x + m;
    } else if (h >= 180.0f && h < 240.0f) {
        dest[0] = m;
        dest[1] = x + m;
        dest[2] = c + m;
    } else if (h >= 240.0f && h < 300.0f) {
        dest[0] = x + m;
        dest[1] = m;
        dest[2] = c + m;
    } else if (h >= 300.0f && h < 360.0f) {
        dest[0] = c + m;
        dest[1] = m;
        dest[2] = x + m;
    } else {
        dest[0] = m;
        dest[1] = m;
        dest[2] = m;
    }
}
