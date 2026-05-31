#include "lib.h"

typedef tflac_u64 tflac_uint;

void tflac_pack_u64le(tflac_u8 *d, tflac_u64 n) {
    d[0] = (tflac_u8)(n);
    d[1] = (tflac_u8)(n >> 8);
    d[2] = (tflac_u8)(n >> 16);
    d[3] = (tflac_u8)(n >> 24);
    d[4] = (tflac_u8)(n >> 32);
    d[5] = (tflac_u8)(n >> 40);
    d[6] = (tflac_u8)(n >> 48);
    d[7] = (tflac_u8)(n >> 56);
}

void tflac_md5_addsample(tflac_md5 *m, tflac_u32 bits,
                                       tflac_uint val) {
    tflac_u32 bytes;
    ((m->total) += (tflac_u64)(bits));
    bytes = bits / 8;
    tflac_u32 pos2 = m->pos % 64;
    tflac_pack_u64le(&m->buffer[pos2], val);
    m->pos += bytes;
    if (m->pos >= 64) {
        m->pos %= 64;
        bytes = m->pos;
        while (bytes--) {
            m->buffer[bytes] = m->buffer[64 + bytes];
        }
    }
}

tflac_u32 update_md5(tflac *t, const tflac_s32 *samples)
    /* The loop runs 5 times, advancing samples by 8 each time and reading
       samples[0..7], so the last access is samples[4*8 + 7] = samples[135]:
       136 elements. md5_ctx.pos < 64 is the running invariant that keeps the
       inner buffer-shift copy (buffer[64 + bytes], buffer is 72 bytes) in
       bounds, since after pos += 8 and pos %= 64 the wrapped pos stays < 8. */
    __CPROVER_requires(__CPROVER_is_fresh(t, sizeof(tflac)) &&
                       t->md5_ctx.pos < 64 &&
                       __CPROVER_is_fresh(samples, sizeof(tflac_s32) * 136))
    __CPROVER_assigns(t->md5_ctx)
{
    tflac_u32 b = t->cur_blocksize * t->channels;
    const tflac_u32 step = sizeof(tflac_uint);
    tflac_uint v;
    for (int i = 0; i <= 4; i++) {
        v = (((tflac_uint)samples[0]) & 0xFF) << 0;
        v |= (((tflac_uint)samples[1]) & 0xFF) << 8;
        v |= (((tflac_uint)samples[2]) & 0xFF) << 16;
        v |= (((tflac_uint)samples[3]) & 0xFF) << 24;
        v |= (((tflac_uint)samples[4]) & 0xFF) << 32;
        v |= (((tflac_uint)samples[5]) & 0xFF) << 40;
        v |= (((tflac_uint)samples[6]) & 0xFF) << 48;
        v |= (((tflac_uint)samples[7]) & 0xFF) << 56;
        tflac_md5_addsample(&t->md5_ctx, (8 * sizeof(tflac_uint)), v);
        b -= step;
        samples += (8 * sizeof(tflac_s32));
    }
    return b;
}

#ifdef CBMC_HARNESS
void update_md5_harness(void) {
    tflac *t;
    const tflac_s32 *samples;
    update_md5(t, samples);
}
#endif

