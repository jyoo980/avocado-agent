#include <math.h>

#include "lib.h"

void hsv_to_rgb(float *dest, const float *src)
__CPROVER_requires(__CPROVER_is_fresh(dest, sizeof(float) * 3))
__CPROVER_requires(__CPROVER_is_fresh(src, sizeof(float) * 3))
__CPROVER_assigns(__CPROVER_object_whole(dest))
{
    float r, g, b;
    float f, p, q, t;
    float h = src[0];
    float s = src[1];
    float v = src[2];
    int i;
    if (s == 0) {
        dest[0] = v;
        dest[1] = v;
        dest[2] = v;
        return;
    }
    h /= 60.0f;
    i = (int)floorf(h);
    f = h - i;
    p = v * (1 - s);
    q = v * (1 - s * f);
    t = v * (1 - s * (1 - f));
    switch (i) {
    case 0:
        r = v;
        g = t;
        b = p;
        break;
    case 1:
        r = q;
        g = v;
        b = p;
        break;
    case 2:
        r = p;
        g = v;
        b = t;
        break;
    case 3:
        r = p;
        g = q;
        b = v;
        break;
    case 4:
        r = t;
        g = p;
        b = v;
        break;
    default:
        r = v;
        g = p;
        b = q;
        break;
    }
    dest[0] = r;
    dest[1] = g;
    dest[2] = b;
}
