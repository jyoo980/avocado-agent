#include "lib.h"

uint32_t rev16(uint32_t a)
    /* Pure unsigned bit-permutation; touches no memory. */
    __CPROVER_assigns()
{
    a = ((a & 0xAAAA) >> 1) | ((a & 0x5555) << 1);
    a = ((a & 0xCCCC) >> 2) | ((a & 0x3333) << 2);
    a = ((a & 0xF0F0) >> 4) | ((a & 0x0F0F) << 4);
    a = ((a & 0xFF00) >> 8) | ((a & 0x00FF) << 8);
    return a;
}

#ifdef CBMC_HARNESS
void rev16_harness(void) {
    uint32_t a;
    rev16(a);
}
#endif
