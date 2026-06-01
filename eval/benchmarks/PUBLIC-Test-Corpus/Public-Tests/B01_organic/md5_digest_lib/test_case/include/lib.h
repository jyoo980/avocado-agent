#include <stdint.h>

typedef uint8_t tflac_u8;
typedef uint32_t tflac_u32;

struct tflac_md5 {
    tflac_u32 a;
    tflac_u32 b;
    tflac_u32 c;
    tflac_u32 d;
};
typedef struct tflac_md5 tflac_md5;

void md5_digest(const tflac_md5 *m, tflac_u8 out[16]);
