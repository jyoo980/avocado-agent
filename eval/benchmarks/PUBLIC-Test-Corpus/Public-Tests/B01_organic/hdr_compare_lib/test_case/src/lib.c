#include "lib.h"

static int hdr_valid(const uint8_t *h) {
    return h[0] == 0xff && ((h[1] & 0xF0) == 0xf0 || (h[1] & 0xFE) == 0xe2) &&
           ((((h[1]) >> 1) & 3) != 0) && (((h[2]) >> 4) != 15) &&
           ((((h[2]) >> 2) & 3) != 3);
}

int hdr_compare(const uint8_t *h1, const uint8_t *h2)
    /* Both headers are read at bytes 0..2. */
    __CPROVER_requires(__CPROVER_is_fresh(h1, 3))
    __CPROVER_requires(__CPROVER_is_fresh(h2, 3))
    __CPROVER_assigns()
{
    return hdr_valid(h2) && ((h1[1] ^ h2[1]) & 0xFE) == 0 &&
           ((h1[2] ^ h2[2]) & 0x0C) == 0 &&
           !((((h1[2]) & 0xF0) == 0) ^ (((h2[2]) & 0xF0) == 0));
}

#ifdef CBMC_HARNESS
void hdr_compare_harness(void) {
    const uint8_t *h1;
    const uint8_t *h2;
    hdr_compare(h1, h2);
}
#endif
