#include "lib.h"

static const tflac_u16 tflac_crc16_tables[8][256];

tflac_u16 crc16(const tflac_u8 *d, tflac_u32 len, tflac_u16 crc16)
    /* len is bounded so the loops unwind fully; d is allocated at the fixed
       maximum so its object size is constant as the pointer advances. */
    __CPROVER_requires(len <= 16 && __CPROVER_is_fresh(d, 16))
    __CPROVER_assigns()
{
    while (len >= 8) {
        crc16 ^= d[0] << 8 | d[1];
        crc16 = tflac_crc16_tables[7][crc16 >> 8] ^
                tflac_crc16_tables[6][crc16 & 0xFF] ^
                tflac_crc16_tables[5][d[2]] ^ tflac_crc16_tables[4][d[3]] ^
                tflac_crc16_tables[3][d[4]] ^ tflac_crc16_tables[2][d[5]] ^
                tflac_crc16_tables[1][d[6]] ^ tflac_crc16_tables[0][d[7]];
        d += 8;
        len -= 8;
    }
    while (len--) {
        crc16 = (crc16 << 8) ^ tflac_crc16_tables[0][(crc16 >> 8) ^ *d++];
    }
    return crc16;
}

#ifdef CBMC_HARNESS
void crc16_harness(void) {
    const tflac_u8 *d;
    tflac_u32 len;
    tflac_u16 c;
    crc16(d, len, c);
}
#endif
