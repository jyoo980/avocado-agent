#include "lib.h"

int bitwriter_add(tflac_bitwriter *bw, tflac_u32 bits,
                                      tflac_uint val)
    /* bits in [1,64] keeps the 64 - bits shift in range; bw->bits < 64 is the
       bitwriter invariant and keeps every val >> bw->bits shift well-defined. */
    __CPROVER_requires(__CPROVER_is_fresh(bw, sizeof(tflac_bitwriter)) &&
                       bits >= 1 && bits <= 64 && bw->bits < 64)
    __CPROVER_assigns(bw->tot, bw->val, bw->bits)
{
    const tflac_uint mask = (18446744073709551615UL) << 1;
    tflac_u32 b;
    int r;
    val <<= ((8 * sizeof(tflac_uint)) - bits);
    bw->tot += bits;
    int i = 0;
    while ((bw->bits + bits >= (8 * sizeof(tflac_uint))) && i < 100) {
        b = (8 * sizeof(tflac_uint)) - bw->bits - 1;
        b = b > bits ? bits : b;
        bw->val |= (val >> bw->bits);
        bw->bits += b;
        bw->val &= mask;
        val <<= b;
        bits -= b;
        i++;
    }
    bw->val |= (val >> bw->bits);
    bw->bits += bits;
    return 0;
}

#ifdef CBMC_HARNESS
void bitwriter_add_harness(void) {
    tflac_bitwriter *bw;
    tflac_u32 bits;
    tflac_uint val;
    bitwriter_add(bw, bits, val);
}
#endif
