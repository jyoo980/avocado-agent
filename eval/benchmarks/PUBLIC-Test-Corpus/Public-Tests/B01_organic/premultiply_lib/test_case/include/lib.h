#include <stdint.h>

typedef struct cp_pixel_t {
    uint8_t r;
    uint8_t g;
    uint8_t b;
    uint8_t a;
} cp_pixel_t;

typedef struct cp_image_t {
    int w;
    int h;
    cp_pixel_t *pix;
} cp_image_t;

void premultiply(cp_image_t *img);
