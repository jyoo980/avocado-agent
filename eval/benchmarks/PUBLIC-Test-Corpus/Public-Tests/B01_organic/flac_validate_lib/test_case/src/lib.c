#include "lib.h"

enum TFLAC_CHANNEL_MODE {
    TFLAC_CHANNEL_INDEPENDENT = 0,
    TFLAC_CHANNEL_LEFT_SIDE = 1,
    TFLAC_CHANNEL_SIDE_RIGHT = 2,
    TFLAC_CHANNEL_MID_SIDE = 3,
    TFLAC_CHANNEL_MODE_COUNT = 4,
};

tflac_u32 tflac_size_memory(tflac_u32 blocksize)
__CPROVER_assigns()
{
    return (tflac_u32)15U + (5U * ((15U + (blocksize * 4U)) & 0xFFFFFFF0U));
}

int flac_validate(tflac *t)
__CPROVER_requires(__CPROVER_is_fresh(t, sizeof(*t)))
__CPROVER_assigns(t->channel_mode, t->max_rice_value, t->partition_order, t->cur_blocksize)
__CPROVER_ensures(__CPROVER_return_value == 0 || __CPROVER_return_value == -1)
{
    if (t->blocksize < 16)
        return -1;
    if (t->blocksize > 65535)
        return -1;
    if (t->samplerate == 0)
        return -1;
    if (t->samplerate > 655350)
        return -1;
    if (t->channels == 0)
        return -1;
    if (t->channels > 8)
        return -1;
    if (t->bitdepth == 0)
        return -1;
    if (t->bitdepth > 32)
        return -1;
    if (t->channel_mode != TFLAC_CHANNEL_INDEPENDENT) {
        if (t->channels != 2 || t->bitdepth == 32) {
            t->channel_mode = TFLAC_CHANNEL_INDEPENDENT;
        }
    }
    if (t->max_rice_value == 0) {
        if (t->bitdepth <= 16) {
            t->max_rice_value = 14;
        } else {
            t->max_rice_value = 30;
        }
    } else if (t->max_rice_value > 30) {
        return -1;
    }
    if (t->max_partition_order > 15) {
        return -1;
    }
    if (t->min_partition_order > t->max_partition_order)
        return -1;
    t->partition_order = t->min_partition_order;
    while ((t->blocksize % (1 << (t->partition_order + 1)) == 0) &&
           t->partition_order < t->max_partition_order) {
        t->partition_order++;
    }
    t->cur_blocksize = t->blocksize;
    return 0;
}
