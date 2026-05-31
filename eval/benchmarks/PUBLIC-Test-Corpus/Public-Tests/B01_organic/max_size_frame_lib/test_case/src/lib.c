#include "lib.h"

tflac_u32 max_size_frame(tflac_u32 blocksize, tflac_u32 channels, tflac_u32 bitdepth)
__CPROVER_requires(blocksize <= 0x1000U && channels <= 8U && bitdepth <= 32U)
__CPROVER_assigns()
__CPROVER_ensures(__CPROVER_return_value >= 18U + channels)
{
    return 18U + channels +
           (((blocksize * bitdepth * (channels * (channels != 2))) +
             (blocksize * bitdepth * (channels == 2)) +
             (blocksize * (bitdepth + (bitdepth != 32)) * (channels == 2)) +
             +7) /
            8);
}
