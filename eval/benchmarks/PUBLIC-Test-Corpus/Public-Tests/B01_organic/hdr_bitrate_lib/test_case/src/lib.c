#include "lib.h"

unsigned hdr_bitrate(const uint8_t *h)
    /* Reads h[1] and h[2]. The layer field ((h[1]>>1)&3) indexes after a -1,
       so it must be non-zero; the bitrate field (h[2]>>4) indexes a 15-entry
       row, so it must not be 15. These hold for any valid MPEG audio header. */
    __CPROVER_requires(__CPROVER_is_fresh(h, 3))
    __CPROVER_requires(((h[1] >> 1) & 3) != 0)
    __CPROVER_requires((h[2] >> 4) != 15)
    __CPROVER_assigns()
{
    static const uint8_t halfrate[2][3][15] = {
        {{0, 4, 8, 12, 16, 20, 24, 28, 32, 40, 48, 56, 64, 72, 80},
         {0, 4, 8, 12, 16, 20, 24, 28, 32, 40, 48, 56, 64, 72, 80},
         {0, 16, 24, 28, 32, 40, 48, 56, 64, 72, 80, 88, 96, 112, 128}},
        {{0, 16, 20, 24, 28, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160},
         {0, 16, 24, 28, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192},
         {0, 16, 32, 48, 64, 80, 96, 112, 128, 144, 160, 176, 192, 208, 224}},
    };
    return 2 *
           halfrate[!!((h[1]) & 0x8)][(((h[1]) >> 1) & 3) - 1][((h[2]) >> 4)];
}

#ifdef CBMC_HARNESS
void hdr_bitrate_harness(void) {
    const uint8_t *h;
    hdr_bitrate(h);
}
#endif
