#include "lib.h"

enum TFLAC_CHANNEL_MODE {
    TFLAC_CHANNEL_INDEPENDENT = 0,
    TFLAC_CHANNEL_LEFT_SIDE = 1,
    TFLAC_CHANNEL_SIDE_RIGHT = 2,
    TFLAC_CHANNEL_MID_SIDE = 3,
    TFLAC_CHANNEL_MODE_COUNT = 4,
};

void update_frame_header(tflac *t)
    __CPROVER_requires(__CPROVER_is_fresh(t, sizeof(tflac)))
    __CPROVER_assigns(t->frame_header)
    __CPROVER_ensures((t->frame_header & 0xFFF80000U) == (0xFFF8U << 16))
    __CPROVER_ensures((t->frame_header & 0x00000001U) == 0)
{
    t->frame_header = 0xFFF8U << 16;
    switch (t->cur_blocksize) {
    case 192:
        t->frame_header |= (0x01U << 12);
        break;
    case 576:
        t->frame_header |= (0x02U << 12);
        break;
    case 1152:
        t->frame_header |= (0x03U << 12);
        break;
    case 2304:
        t->frame_header |= (0x04U << 12);
        break;
    case 4608:
        t->frame_header |= (0x05U << 12);
        break;
    case 256:
        t->frame_header |= (0x08U << 12);
        break;
    case 512:
        t->frame_header |= (0x09U << 12);
        break;
    case 1024:
        t->frame_header |= (0x0AU << 12);
        break;
    case 2048:
        t->frame_header |= (0x0BU << 12);
        break;
    case 4096:
        t->frame_header |= (0x0CU << 12);
        break;
    case 8192:
        t->frame_header |= (0x0DU << 12);
        break;
    case 16384:
        t->frame_header |= (0x0EU << 12);
        break;
    case 32768:
        t->frame_header |= (0x0FU << 12);
        break;
    default: {
        t->frame_header |=
            t->cur_blocksize <= 256 ? (0x06U << 12) : (0x07U << 12);
    }
    }
    switch (t->samplerate) {
    case 882000:
        t->frame_header |= (0x01U << 8);
        break;
    case 176400:
        t->frame_header |= (0x02U << 8);
        break;
    case 192000:
        t->frame_header |= (0x03U << 8);
        break;
    case 8000:
        t->frame_header |= (0x04U << 8);
        break;
    case 16000:
        t->frame_header |= (0x05U << 8);
        break;
    case 22050:
        t->frame_header |= (0x06U << 8);
        break;
    case 24000:
        t->frame_header |= (0x07U << 8);
        break;
    case 32000:
        t->frame_header |= (0x08U << 8);
        break;
    case 44100:
        t->frame_header |= (0x09U << 8);
        break;
    case 48000:
        t->frame_header |= (0x0AU << 8);
        break;
    case 96000:
        t->frame_header |= (0x0BU << 8);
        break;
    default: {
        if (t->samplerate % 1000 == 0) {
            if (t->samplerate / 1000 < 256) {
                t->frame_header |= (0x0CU << 8);
            }
        } else if (t->samplerate < 65536) {
            t->frame_header |= (0x0DU << 8);
        } else if (t->samplerate % 10 == 0) {
            if (t->samplerate / 10 < 65536) {
                t->frame_header |= (0x0EU << 8);
            }
        }
    }
    }
    tflac_u8 mode = t->channel_mode % 4;
    switch ((enum TFLAC_CHANNEL_MODE)mode) {
    case TFLAC_CHANNEL_INDEPENDENT:
        t->frame_header |= (t->channels - 1) << 4;
        break;
    case TFLAC_CHANNEL_LEFT_SIDE:
        t->frame_header |= (0x08) << 4;
        break;
    case TFLAC_CHANNEL_SIDE_RIGHT:
        t->frame_header |= (0x09) << 4;
        break;
    case TFLAC_CHANNEL_MID_SIDE:
        t->frame_header |= (0x0A) << 4;
        break;
    default:
        break;
    }
    switch (t->bitdepth) {
    case 8:
        t->frame_header |= (1U << 1);
        break;
    case 12:
        t->frame_header |= (2U << 1);
        break;
    case 16:
        t->frame_header |= (4U << 1);
        break;
    case 20:
        t->frame_header |= (5U << 1);
        break;
    case 24:
        t->frame_header |= (6U << 1);
        break;
    case 32:
        t->frame_header |= (7U << 1);
        break;
    default:
        break;
    }
}
