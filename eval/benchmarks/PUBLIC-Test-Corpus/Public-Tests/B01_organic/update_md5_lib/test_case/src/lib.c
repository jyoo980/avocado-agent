#include "lib.h"

typedef tflac_u64 tflac_uint;

void tflac_pack_u64le(tflac_u8 *d, tflac_u64 n)
    __CPROVER_requires(__CPROVER_is_fresh(d, 8 * sizeof(tflac_u8)))
    __CPROVER_assigns(d[0], d[1], d[2], d[3], d[4], d[5], d[6], d[7])
    __CPROVER_ensures(d[0] == (tflac_u8)(n))
    __CPROVER_ensures(d[1] == (tflac_u8)(n >> 8))
    __CPROVER_ensures(d[2] == (tflac_u8)(n >> 16))
    __CPROVER_ensures(d[3] == (tflac_u8)(n >> 24))
    __CPROVER_ensures(d[4] == (tflac_u8)(n >> 32))
    __CPROVER_ensures(d[5] == (tflac_u8)(n >> 40))
    __CPROVER_ensures(d[6] == (tflac_u8)(n >> 48))
    __CPROVER_ensures(d[7] == (tflac_u8)(n >> 56))
{
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
                                       tflac_uint val)
    __CPROVER_requires(__CPROVER_is_fresh(m, sizeof(tflac_md5)))
    __CPROVER_requires(bits <= 64)
    __CPROVER_assigns(m->total, m->pos, __CPROVER_object_whole(m->buffer))
    __CPROVER_ensures(m->pos < 64)
    __CPROVER_ensures(m->total == __CPROVER_old(m->total) + (tflac_u64)bits)
{
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
    __CPROVER_requires(__CPROVER_is_fresh(t, sizeof(tflac)))
    __CPROVER_requires(__CPROVER_is_fresh(samples, 136 * sizeof(tflac_s32)))
    __CPROVER_assigns(t->md5_ctx)
    __CPROVER_ensures(__CPROVER_return_value ==
                      (tflac_u32)(__CPROVER_old(t->cur_blocksize) *
                                      __CPROVER_old(t->channels) -
                                  5 * (tflac_u32)sizeof(tflac_u64)))
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

