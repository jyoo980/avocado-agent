#include "lib.h"

tflac_u32 max_size_frame(tflac_u32 blocksize, tflac_u32 channels, tflac_u32 bitdepth)
    __CPROVER_assigns()
{
    return 18U + channels +
           (((blocksize * bitdepth * (channels * (channels != 2))) +
             (blocksize * bitdepth * (channels == 2)) +
             (blocksize * (bitdepth + (bitdepth != 32)) * (channels == 2)) +
             +7) /
            8);
}

#ifdef CBMC_HARNESS
void max_size_frame_harness(void) {
    tflac_u32 blocksize, channels, bitdepth;
    max_size_frame(blocksize, channels, bitdepth);
}
#endif
