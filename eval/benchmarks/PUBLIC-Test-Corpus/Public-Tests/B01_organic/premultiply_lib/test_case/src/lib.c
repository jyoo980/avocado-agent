#include "lib.h"

void premultiply(cp_image_t *img)
    /* w,h bounded so the byte-stride loop unwinds fully; pixels allocated at the
       fixed maximum (w*h <= 16) so the buffer's object size is constant. */
    __CPROVER_requires(__CPROVER_is_fresh(img, sizeof(cp_image_t)) &&
                       img->w >= 0 && img->w <= 4 &&
                       img->h >= 0 && img->h <= 4 &&
                       __CPROVER_is_fresh(img->pix, sizeof(cp_pixel_t) * 16))
    __CPROVER_assigns(__CPROVER_object_whole(img->pix))
{
    int w = img->w;
    int h = img->h;
    int stride = w * sizeof(cp_pixel_t);
    uint8_t *data = (uint8_t *)img->pix;
    for (int i = 0; i < (int)stride * h; i += sizeof(cp_pixel_t)) {
        float a = (float)data[i + 3] / 255.0f;
        float r = (float)data[i + 0] / 255.0f;
        float g = (float)data[i + 1] / 255.0f;
        float b = (float)data[i + 2] / 255.0f;
        r *= a;
        g *= a;
        b *= a;
        data[i + 0] = (uint8_t)(r * 255.0f);
        data[i + 1] = (uint8_t)(g * 255.0f);
        data[i + 2] = (uint8_t)(b * 255.0f);
    }
}

#ifdef CBMC_HARNESS
void premultiply_harness(void) {
    cp_image_t *img;
    premultiply(img);
}
#endif
