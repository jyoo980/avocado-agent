#include "lib.h"

static uint32_t get_bits(bs_t *bs, int n)
    /* get_bits returns an n-bit value (or 0 past the limit), so the result has
       at most n significant bits; modelled here so the caller's (int)result -
       half stays free of signed overflow. */
    __CPROVER_requires(__CPROVER_r_ok(bs, sizeof(bs_t)))
    __CPROVER_assigns(bs->pos)
    __CPROVER_ensures(!(n >= 0 && n < 32) ||
                      __CPROVER_return_value < (1u << n))
{
    uint32_t next, cache = 0, s = bs->pos & 7;
    int shl = n + s;
    const uint8_t *p = bs->buf + (bs->pos >> 3);
    if ((bs->pos += n) > bs->limit)
        return 0;
    next = *p++ & (255 >> s);
    while ((shl -= 8) > 0) {
        cache |= next << shl;
        next = *p++;
    }
    return cache | (next >> -shl);
}

int dequantize_granule(float *grbuf, bs_t *bs, L12_scale_info *sci,
                                  int group_size)
    /* Bounded proof: total_bands <= 1 keeps i in {0,1} so only bitalloc[0..1]
       are read; bounding those <= 16 stays in the ba<17 branch (avoids the
       2<<(ba-17) shift overflow). group_size <= 2 and the alternating choff
       (576/-558) put the furthest write at 3*group_size + 576 + group_size-1
       = 583, so grbuf is allocated with 640 floats. get_bits is abstracted by
       its own contract (assigns bs->pos), so the bit buffer is not exercised
       here. */
    __CPROVER_requires(group_size >= 1 && group_size <= 2 &&
                       __CPROVER_is_fresh(grbuf, sizeof(float) * 640) &&
                       __CPROVER_is_fresh(bs, sizeof(bs_t)) &&
                       __CPROVER_is_fresh(sci, sizeof(L12_scale_info)) &&
                       sci->total_bands <= 1 &&
                       sci->bitalloc[0] <= 16 && sci->bitalloc[1] <= 16)
    __CPROVER_assigns(__CPROVER_object_whole(grbuf), bs->pos)
{
    int i, j, k, choff = 576;
    for (j = 0; j < 4; j++) {
        float *dst = grbuf + group_size * j;
        for (i = 0; i < 2 * sci->total_bands; i++) {
            int ba = sci->bitalloc[i];
            if (ba != 0) {
                if (ba < 17) {
                    int half = (1 << (ba - 1)) - 1;
                    for (k = 0; k < group_size; k++) {
                        dst[k] = (float)((int)get_bits(bs, ba) - half);
                    }
                } else {
                    unsigned mod = (2 << (ba - 17)) + 1;
                    unsigned code = get_bits(bs, mod + 2 - (mod >> 3));
                    for (k = 0; k < group_size; k++, code /= mod) {
                        dst[k] = (float)((int)(code % mod - mod / 2));
                    }
                }
            }
            dst += choff;
            choff = 18 - choff;
        }
    }
    return group_size * 4;
}

#ifdef CBMC_HARNESS
void dequantize_granule_harness(void) {
    float *grbuf;
    bs_t *bs;
    L12_scale_info *sci;
    int group_size;
    dequantize_granule(grbuf, bs, sci, group_size);
}
#endif
