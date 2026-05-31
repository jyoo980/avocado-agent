#include "lib.h"

int encode_quant(int uni, int step, int pred, int tgt, int tgt2, int lsbit)
    /* ADPCM-style quantiser: indices/steps/samples live in the 16-bit
       sample domain, which keeps every intermediate within int range. */
    __CPROVER_requires(uni >= -32768 && uni <= 32767)
    __CPROVER_requires(step >= -32768 && step <= 32767)
    __CPROVER_requires(pred >= -32768 && pred <= 32767)
    __CPROVER_requires(tgt >= -32768 && tgt <= 32767)
    __CPROVER_requires(tgt2 >= -32768 && tgt2 <= 32767)
    __CPROVER_assigns()
{
    int uni1, uni2;
    int diff, p0, p1, p2, p3, d0, d1, d2, d3;
    uni1 = uni + 1;
    uni2 = uni - 1;
    if ((uni ^ uni1) & (~7))
        uni1 = uni;
    if ((uni ^ uni2) & (~7))
        uni2 = uni;
    if (lsbit) {
        if (lsbit == 4) {
            uni &= ~1;
            uni1 &= ~1;
            uni2 &= ~1;
            uni |= (uni >> 1) & (uni >> 2) & 1;
            uni1 |= (uni1 >> 1) & (uni1 >> 2) & 1;
            uni2 |= (uni2 >> 1) & (uni2 >> 2) & 1;
        } else if (lsbit & 1) {
            uni |= 1;
            uni1 |= 1;
            uni2 |= 1;
        } else {
            uni &= ~1;
            uni1 &= ~1;
            uni2 &= ~1;
        }
    }
    diff = ((2 * (uni & 7) + 1) * step) / 8;
    if (uni & 8)
        diff = -diff;
    p0 = pred + diff;
    d0 = tgt - p0;
    d0 = d0 ^ (d0 >> 31);
    diff = ((2 * (uni1 & 7) + 1) * step) / 8;
    if (uni1 & 8)
        diff = -diff;
    p1 = pred + diff;
    d1 = tgt - p1;
    d1 = d1 ^ (d1 >> 31);
    diff = ((2 * (uni2 & 7) + 1) * step) / 8;
    if (uni2 & 8)
        diff = -diff;
    p2 = pred + diff;
    d2 = tgt - p2;
    d2 = d2 ^ (d2 >> 31);
    d3 = tgt2 - p0;
    d3 = d3 ^ (d3 >> 31);
    d0 += d3 >> 5;
    d3 = tgt2 - p1;
    d3 = d3 ^ (d3 >> 31);
    d1 += d3 >> 5;
    d3 = tgt2 - p2;
    d3 = d3 ^ (d3 >> 31);
    d2 += d3 >> 5;
    if (d1 < d0)
        uni = uni1;
    if (d2 < d0)
        uni = uni2;
    return (uni);
}

#ifdef CBMC_HARNESS
void encode_quant_harness(void) {
    int uni, step, pred, tgt, tgt2, lsbit;
    encode_quant(uni, step, pred, tgt, tgt2, lsbit);
}
#endif
