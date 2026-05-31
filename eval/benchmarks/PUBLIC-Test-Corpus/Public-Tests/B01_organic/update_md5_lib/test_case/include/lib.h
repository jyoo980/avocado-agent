#include <stdint.h>

typedef uint8_t tflac_u8;
typedef int32_t tflac_s32;
typedef uint32_t tflac_u32;
typedef uint64_t tflac_u64;

struct tflac_md5 {
    tflac_u32 pos;
    tflac_u64 total;
    tflac_u8 buffer[64 + 8];
};
typedef struct tflac_md5 tflac_md5;

struct tflac {
    tflac_md5 md5_ctx;
    tflac_u32 cur_blocksize;
    tflac_u32 channels;
};
typedef struct tflac tflac;

tflac_u32 update_md5(tflac *t, const tflac_s32 *samples);
