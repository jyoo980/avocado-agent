#include "lib.h"

static int hdr_valid(const uint8_t *h)
    __CPROVER_requires(__CPROVER_is_fresh(h, 3 * sizeof(uint8_t)))
    __CPROVER_assigns()
    __CPROVER_ensures(__CPROVER_return_value == 0 || __CPROVER_return_value == 1)
{
    return h[0] == 0xff && ((h[1] & 0xF0) == 0xf0 || (h[1] & 0xFE) == 0xe2) &&
           ((((h[1]) >> 1) & 3) != 0) && (((h[2]) >> 4) != 15) &&
           ((((h[2]) >> 2) & 3) != 3);
}

int hdr_compare(const uint8_t *h1, const uint8_t *h2)
    __CPROVER_requires(__CPROVER_is_fresh(h1, 3 * sizeof(uint8_t)))
    __CPROVER_requires(__CPROVER_is_fresh(h2, 3 * sizeof(uint8_t)))
    __CPROVER_assigns()
    __CPROVER_ensures(__CPROVER_return_value == 0 || __CPROVER_return_value == 1)
{
    return hdr_valid(h2) && ((h1[1] ^ h2[1]) & 0xFE) == 0 &&
           ((h1[2] ^ h2[2]) & 0x0C) == 0 &&
           !((((h1[2]) & 0xF0) == 0) ^ (((h2[2]) & 0xF0) == 0));
}
