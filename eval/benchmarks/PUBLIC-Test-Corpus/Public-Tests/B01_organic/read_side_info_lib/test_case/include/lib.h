#include <stdint.h>

typedef struct {
    const uint8_t *buf;
    int pos, limit;
} bs_t;

typedef struct {
    const uint8_t *sfbtab;
    uint16_t part_23_length, big_values, scalefac_compress;
    uint8_t global_gain, block_type, mixed_block_flag, n_long_sfb, n_short_sfb;
    uint8_t table_select[3], region_count[3], subblock_gain[3];
    uint8_t preflag, scalefac_scale, count1_table, scfsi;
} L3_gr_info_t;

int read_side_info(bs_t *bs, L3_gr_info_t *gr, const uint8_t *hdr);
