#include <stdint.h>

typedef uint8_t tflac_u8;
typedef uint32_t tflac_u32;
typedef uint64_t tflac_u64;
typedef tflac_u64 tflac_uint;

struct tflac_bitwriter {
    tflac_uint val;
    tflac_u32 bits;
    tflac_u32 pos;
    tflac_u32 len;
    tflac_u32 tot;
    tflac_u8 *buffer;
};
typedef struct tflac_bitwriter tflac_bitwriter;

int bitwriter_add(tflac_bitwriter *bw, tflac_u32 bits,
                                      tflac_uint val);
