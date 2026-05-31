#include "lib.h"

static uint32_t get_bits(bs_t *bs, int n)
    /* Models the n-bit read: bs->pos always advances by n, and the result has
       at most n significant bits. Abstracting it keeps the bit buffer out of
       the proof and pins bs->pos so the final overflow checks stay in range. */
    __CPROVER_requires(__CPROVER_r_ok(bs, sizeof(bs_t)))
    __CPROVER_assigns(bs->pos)
    __CPROVER_ensures(bs->pos == __CPROVER_old(bs->pos) + n &&
                      (!(n >= 0 && n < 32) || __CPROVER_return_value < (1u << n)))
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

int read_side_info(bs_t *bs, L3_gr_info_t *gr, const uint8_t *hdr)
    /* (hdr[3]&0xC0)==0xC0 makes the base granule count 1 and !(hdr[1]&8) keeps
       it from doubling, so gr_count == 1 and the do/while writes gr[0]: gr is
       allocated with 1 element. sr_idx indexes the 8-row scalefactor tables;
       (hdr[2]>>2)&3 != 3 keeps it <= 7. bs->pos starts at 0 and bs->limit is
       bounded so the final part_23/limit arithmetic does not signed-overflow.
       get_bits is abstracted. */
    __CPROVER_requires(__CPROVER_is_fresh(bs, sizeof(bs_t)) && bs->pos == 0 &&
                       bs->limit >= 0 && bs->limit <= 100000 &&
                       __CPROVER_is_fresh(gr, sizeof(L3_gr_info_t) * 1) &&
                       __CPROVER_is_fresh(hdr, 4) &&
                       ((hdr[2] >> 2) & 3) != 3 &&
                       ((hdr[3] & 0xC0) == 0xC0) &&
                       !((hdr[1] & 0x8)))
    __CPROVER_assigns(__CPROVER_object_whole(gr), bs->pos)
{
    static const uint8_t g_scf_long[8][23] = {
        {6,  6,  6,  6,  6,  6,  8,  10, 12, 14, 16, 20,
         24, 28, 32, 38, 46, 52, 60, 68, 58, 54, 0},
        {12, 12, 12, 12, 12, 12, 16, 20, 24, 28, 32, 40,
         48, 56, 64, 76, 90, 2,  2,  2,  2,  2,  0},
        {6,  6,  6,  6,  6,  6,  8,  10, 12, 14, 16, 20,
         24, 28, 32, 38, 46, 52, 60, 68, 58, 54, 0},
        {6,  6,  6,  6,  6,  6,  8,  10, 12, 14, 16, 18,
         22, 26, 32, 38, 46, 54, 62, 70, 76, 36, 0},
        {6,  6,  6,  6,  6,  6,  8,  10, 12, 14, 16, 20,
         24, 28, 32, 38, 46, 52, 60, 68, 58, 54, 0},
        {4,  4,  4,  4,  4,  4,  6,  6,  8,  8,   10, 12,
         16, 20, 24, 28, 34, 42, 50, 54, 76, 158, 0},
        {4,  4,  4,  4,  4,  4,  6,  6,  6,  8,   10, 12,
         16, 18, 22, 28, 34, 40, 46, 54, 54, 192, 0},
        {4,  4,  4,  4,  4,  4,  6,  6,  8,   10, 12, 16,
         20, 24, 30, 38, 46, 56, 68, 84, 102, 26, 0}};
    static const uint8_t g_scf_short[8][40] = {
        {4,  4,  4,  4,  4,  4,  4,  4,  4,  6,  6,  6,  8,  8,
         8,  10, 10, 10, 12, 12, 12, 14, 14, 14, 18, 18, 18, 24,
         24, 24, 30, 30, 30, 40, 40, 40, 18, 18, 18, 0},
        {8,  8,  8,  8,  8,  8,  8,  8,  8,  12, 12, 12, 16, 16,
         16, 20, 20, 20, 24, 24, 24, 28, 28, 28, 36, 36, 36, 2,
         2,  2,  2,  2,  2,  2,  2,  2,  26, 26, 26, 0},
        {4,  4,  4,  4,  4,  4,  4,  4,  4,  6,  6,  6,  6,  6,
         6,  8,  8,  8,  10, 10, 10, 14, 14, 14, 18, 18, 18, 26,
         26, 26, 32, 32, 32, 42, 42, 42, 18, 18, 18, 0},
        {4,  4,  4,  4,  4,  4,  4,  4,  4,  6,  6,  6,  8,  8,
         8,  10, 10, 10, 12, 12, 12, 14, 14, 14, 18, 18, 18, 24,
         24, 24, 32, 32, 32, 44, 44, 44, 12, 12, 12, 0},
        {4,  4,  4,  4,  4,  4,  4,  4,  4,  6,  6,  6,  8,  8,
         8,  10, 10, 10, 12, 12, 12, 14, 14, 14, 18, 18, 18, 24,
         24, 24, 30, 30, 30, 40, 40, 40, 18, 18, 18, 0},
        {4,  4,  4,  4,  4,  4,  4,  4,  4,  4,  4,  4,  6,  6,
         6,  8,  8,  8,  10, 10, 10, 12, 12, 12, 14, 14, 14, 18,
         18, 18, 22, 22, 22, 30, 30, 30, 56, 56, 56, 0},
        {4,  4,  4,  4,  4,  4,  4,  4,  4,  4,  4,  4,  6,  6,
         6,  6,  6,  6,  10, 10, 10, 12, 12, 12, 14, 14, 14, 16,
         16, 16, 20, 20, 20, 26, 26, 26, 66, 66, 66, 0},
        {4,  4,  4,  4,  4,  4,  4,  4,  4,  4,  4,  4,  6,  6,
         6,  8,  8,  8,  12, 12, 12, 16, 16, 16, 20, 20, 20, 26,
         26, 26, 34, 34, 34, 42, 42, 42, 12, 12, 12, 0}};
    static const uint8_t g_scf_mixed[8][40] = {
        {6,  6,  6,  6,  6,  6,  6,  6,  6,  8,  8,  8,  10,
         10, 10, 12, 12, 12, 14, 14, 14, 18, 18, 18, 24, 24,
         24, 30, 30, 30, 40, 40, 40, 18, 18, 18, 0},
        {12, 12, 12, 4,  4,  4,  8,  8,  8,  12, 12, 12, 16, 16,
         16, 20, 20, 20, 24, 24, 24, 28, 28, 28, 36, 36, 36, 2,
         2,  2,  2,  2,  2,  2,  2,  2,  26, 26, 26, 0},
        {6,  6,  6,  6,  6,  6,  6,  6,  6,  6,  6,  6,  8,
         8,  8,  10, 10, 10, 14, 14, 14, 18, 18, 18, 26, 26,
         26, 32, 32, 32, 42, 42, 42, 18, 18, 18, 0},
        {6,  6,  6,  6,  6,  6,  6,  6,  6,  8,  8,  8,  10,
         10, 10, 12, 12, 12, 14, 14, 14, 18, 18, 18, 24, 24,
         24, 32, 32, 32, 44, 44, 44, 12, 12, 12, 0},
        {6,  6,  6,  6,  6,  6,  6,  6,  6,  8,  8,  8,  10,
         10, 10, 12, 12, 12, 14, 14, 14, 18, 18, 18, 24, 24,
         24, 30, 30, 30, 40, 40, 40, 18, 18, 18, 0},
        {4,  4,  4,  4,  4,  4,  6,  6,  4,  4,  4,  6,  6,
         6,  8,  8,  8,  10, 10, 10, 12, 12, 12, 14, 14, 14,
         18, 18, 18, 22, 22, 22, 30, 30, 30, 56, 56, 56, 0},
        {4,  4,  4,  4,  4,  4,  6,  6,  4,  4,  4,  6,  6,
         6,  6,  6,  6,  10, 10, 10, 12, 12, 12, 14, 14, 14,
         16, 16, 16, 20, 20, 20, 26, 26, 26, 66, 66, 66, 0},
        {4,  4,  4,  4,  4,  4,  6,  6,  4,  4,  4,  6,  6,
         6,  8,  8,  8,  12, 12, 12, 16, 16, 16, 20, 20, 20,
         26, 26, 26, 34, 34, 34, 42, 42, 42, 12, 12, 12, 0}};
    unsigned tables, scfsi = 0;
    int main_data_begin, part_23_sum = 0;
    int sr_idx = ((((hdr[2]) >> 2) & 3) +
                  (((hdr[1] >> 3) & 1) + ((hdr[1] >> 4) & 1)) * 3);
    sr_idx -= (sr_idx != 0);
    int gr_count = (((hdr[3]) & 0xC0) == 0xC0) ? 1 : 2;
    if (((hdr[1]) & 0x8)) {
        gr_count *= 2;
        main_data_begin = get_bits(bs, 9);
        scfsi = get_bits(bs, 7 + gr_count);
    } else {
        main_data_begin = get_bits(bs, 8 + gr_count) >> gr_count;
    }
    do {
        if ((((hdr[3]) & 0xC0) == 0xC0)) {
            scfsi <<= 4;
        }
        gr->part_23_length = (uint16_t)get_bits(bs, 12);
        part_23_sum += gr->part_23_length;
        gr->big_values = (uint16_t)get_bits(bs, 9);
        if (gr->big_values > 288) {
            return -1;
        }
        gr->global_gain = (uint8_t)get_bits(bs, 8);
        gr->scalefac_compress =
            (uint16_t)get_bits(bs, ((hdr[1]) & 0x8) ? 4 : 9);
        gr->sfbtab = g_scf_long[sr_idx];
        gr->n_long_sfb = 22;
        gr->n_short_sfb = 0;
        if (get_bits(bs, 1)) {
            gr->block_type = (uint8_t)get_bits(bs, 2);
            if (!gr->block_type) {
                return -1;
            }
            gr->mixed_block_flag = (uint8_t)get_bits(bs, 1);
            gr->region_count[0] = 7;
            gr->region_count[1] = 255;
            if (gr->block_type == 2) {
                scfsi &= 0x0F0F;
                if (!gr->mixed_block_flag) {
                    gr->region_count[0] = 8;
                    gr->sfbtab = g_scf_short[sr_idx];
                    gr->n_long_sfb = 0;
                    gr->n_short_sfb = 39;
                } else {
                    gr->sfbtab = g_scf_mixed[sr_idx];
                    gr->n_long_sfb = ((hdr[1]) & 0x8) ? 8 : 6;
                    gr->n_short_sfb = 30;
                }
            }
            tables = get_bits(bs, 10);
            tables <<= 5;
            gr->subblock_gain[0] = (uint8_t)get_bits(bs, 3);
            gr->subblock_gain[1] = (uint8_t)get_bits(bs, 3);
            gr->subblock_gain[2] = (uint8_t)get_bits(bs, 3);
        } else {
            gr->block_type = 0;
            gr->mixed_block_flag = 0;
            tables = get_bits(bs, 15);
            gr->region_count[0] = (uint8_t)get_bits(bs, 4);
            gr->region_count[1] = (uint8_t)get_bits(bs, 3);
            gr->region_count[2] = 255;
        }
        gr->table_select[0] = (uint8_t)(tables >> 10);
        gr->table_select[1] = (uint8_t)((tables >> 5) & 31);
        gr->table_select[2] = (uint8_t)((tables) & 31);
        gr->preflag =
            ((hdr[1]) & 0x8) ? get_bits(bs, 1) : (gr->scalefac_compress >= 500);
        gr->scalefac_scale = (uint8_t)get_bits(bs, 1);
        gr->count1_table = (uint8_t)get_bits(bs, 1);
        gr->scfsi = (uint8_t)((scfsi >> 12) & 15);
        scfsi <<= 4;
        gr++;
    } while (--gr_count);
    if (part_23_sum + bs->pos > bs->limit + main_data_begin * 8) {
        return -1;
    }
    return main_data_begin;
}

#ifdef CBMC_HARNESS
void read_side_info_harness(void) {
    bs_t *bs;
    L3_gr_info_t *gr;
    const uint8_t *hdr;
    read_side_info(bs, gr, hdr);
}
#endif
