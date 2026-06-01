#include "lib.h"

static uint32_t get_bits(bs_t *bs, int n)
__CPROVER_requires(__CPROVER_is_fresh(bs, sizeof(*bs)))
__CPROVER_requires(0 <= n && n <= 32)
__CPROVER_requires(0 <= bs->pos && bs->pos <= bs->limit)
__CPROVER_requires(bs->limit <= 8192)
__CPROVER_requires(__CPROVER_is_fresh(bs->buf, ((bs->limit + n) / 8 + 8) * sizeof(*bs->buf)))
__CPROVER_assigns(bs->pos)
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
__CPROVER_requires(__CPROVER_is_fresh(bs, sizeof(*bs)))
__CPROVER_requires(__CPROVER_is_fresh(sci, sizeof(*sci)))
__CPROVER_requires(0 <= bs->pos && bs->pos <= bs->limit && bs->limit <= 8192)
__CPROVER_requires(__CPROVER_is_fresh(bs->buf, (bs->limit / 8 + 16) * sizeof(*bs->buf)))
__CPROVER_requires(sci->total_bands <= 1)
__CPROVER_requires(group_size >= 0 && group_size <= 2)
__CPROVER_requires(__CPROVER_forall { int q; (0 <= q && q < 64) ==> sci->bitalloc[q] <= 16 })
__CPROVER_requires(__CPROVER_is_fresh(grbuf, 4096 * sizeof(*grbuf)))
__CPROVER_assigns(bs->pos, __CPROVER_object_whole(grbuf))
__CPROVER_ensures(__CPROVER_return_value == group_size * 4)
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
