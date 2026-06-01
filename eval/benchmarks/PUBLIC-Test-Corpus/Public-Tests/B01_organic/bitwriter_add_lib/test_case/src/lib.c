#include "lib.h"

int bitwriter_add(tflac_bitwriter *bw, tflac_u32 bits,
                                      tflac_uint val)
__CPROVER_requires(__CPROVER_is_fresh(bw, sizeof(*bw)))
__CPROVER_requires(bits >= 1 && bits <= 64)
__CPROVER_requires(bw->bits < 64)
__CPROVER_assigns(bw->tot, bw->val, bw->bits)
__CPROVER_ensures(__CPROVER_return_value == 0)
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
