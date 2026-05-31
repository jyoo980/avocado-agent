#include "lib.h"

void flip_horizontal(cp_image_t *img)
    /* w,h are bounded so both loops unwind fully; the pixel buffer is allocated
       at the fixed maximum (w*h <= 16) so its object size is constant and the
       symbolic w*i row offsets stay provably in bounds. */
    __CPROVER_requires(__CPROVER_is_fresh(img, sizeof(cp_image_t)) &&
                       img->w >= 0 && img->w <= 4 &&
                       img->h >= 0 && img->h <= 4 &&
                       __CPROVER_is_fresh(img->pix, sizeof(cp_pixel_t) * 16))
    __CPROVER_assigns(__CPROVER_object_whole(img->pix))
{
    cp_pixel_t *pix = img->pix;
    int w = img->w;
    int h = img->h;
    int flips = h / 2;
    for (int i = 0; i < flips; ++i) {
        cp_pixel_t *a = pix + w * i;
        cp_pixel_t *b = pix + w * (h - i - 1);
        for (int j = 0; j < w; ++j) {
            cp_pixel_t t = *a;
            *a = *b;
            *b = t;
            ++a;
            ++b;
        }
    }
}

#ifdef CBMC_HARNESS
void flip_horizontal_harness(void) {
    cp_image_t *img;
    flip_horizontal(img);
}
#endif
