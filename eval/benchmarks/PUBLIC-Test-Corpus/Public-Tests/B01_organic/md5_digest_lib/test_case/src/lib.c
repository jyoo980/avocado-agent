#include "lib.h"

void md5_digest(const tflac_md5 *m, tflac_u8 out[16])
__CPROVER_requires(__CPROVER_is_fresh(m, sizeof(*m)))
__CPROVER_requires(__CPROVER_is_fresh(out, 16 * sizeof(*out)))
__CPROVER_assigns(__CPROVER_object_upto(out, 16 * sizeof(*out)))
__CPROVER_ensures(out[0] == (tflac_u8)(m->a) && out[15] == (tflac_u8)(m->d >> 24))
{
    out[0] = (tflac_u8)(m->a);
    out[1] = (tflac_u8)(m->a >> 8);
    out[2] = (tflac_u8)(m->a >> 16);
    out[3] = (tflac_u8)(m->a >> 24);
    out[4] = (tflac_u8)(m->b);
    out[5] = (tflac_u8)(m->b >> 8);
    out[6] = (tflac_u8)(m->b >> 16);
    out[7] = (tflac_u8)(m->b >> 24);
    out[8] = (tflac_u8)(m->c);
    out[9] = (tflac_u8)(m->c >> 8);
    out[10] = (tflac_u8)(m->c >> 16);
    out[11] = (tflac_u8)(m->c >> 24);
    out[12] = (tflac_u8)(m->d);
    out[13] = (tflac_u8)(m->d >> 8);
    out[14] = (tflac_u8)(m->d >> 16);
    out[15] = (tflac_u8)(m->d >> 24);
}

