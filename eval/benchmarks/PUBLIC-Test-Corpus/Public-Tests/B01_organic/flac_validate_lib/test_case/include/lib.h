#include <stdint.h>

typedef uint8_t tflac_u8;
typedef uint32_t tflac_u32;

struct tflac {
    tflac_u32 blocksize;
    tflac_u32 samplerate;
    tflac_u32 channels;
    tflac_u32 bitdepth;
    tflac_u8 channel_mode;
    tflac_u8 max_rice_value;
    tflac_u8 min_partition_order;
    tflac_u8 max_partition_order;
    tflac_u8 partition_order;
    tflac_u32 cur_blocksize;
};
typedef struct tflac tflac;

int flac_validate(tflac *t);
