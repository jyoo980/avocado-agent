#include <stdint.h>

typedef struct {
    const uint8_t *buf;
    int pos, limit;
} bs_t;

typedef struct {
    float scf[3 * 64];
    uint8_t total_bands, stereo_bands, bitalloc[64], scfcod[64];
} L12_scale_info;

int dequantize_granule(float *grbuf, bs_t *bs, L12_scale_info *sci,
                                  int group_size);
