#include <stdint.h>

typedef uint8_t tflac_u8;
typedef uint32_t tflac_u32;

struct tflac {
    tflac_u32 samplerate;
    tflac_u32 channels;
    tflac_u32 bitdepth;
    tflac_u8 channel_mode;
    tflac_u32 frame_header;
    tflac_u32 cur_blocksize;
};
typedef struct tflac tflac;

void update_frame_header(tflac *t);
