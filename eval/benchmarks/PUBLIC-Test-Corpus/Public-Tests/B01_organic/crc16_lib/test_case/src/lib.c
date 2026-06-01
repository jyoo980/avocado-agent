#include "lib.h"

static const tflac_u16 tflac_crc16_tables[8][256];

tflac_u16 crc16(const tflac_u8 *d, tflac_u32 len, tflac_u16 crc16)
__CPROVER_requires(len <= 8)
__CPROVER_requires(__CPROVER_is_fresh(d, len))
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
