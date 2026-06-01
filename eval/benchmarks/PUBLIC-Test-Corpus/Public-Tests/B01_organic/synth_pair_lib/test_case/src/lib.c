#include "lib.h"

static int16_t mp3d_scale_pcm(float sample)
__CPROVER_assigns()
{
    if (sample >= 32766.5)
        return (int16_t)32767;
    if (sample <= -32767.5)
        return (int16_t)-32768;
    int16_t s = (int16_t)(sample + .5f);
    s -= (s < 0);
    return s;
}

void synth_pair(mp3d_sample_t *pcm, int nch, const float *z)
__CPROVER_requires(nch >= 1 && nch <= 2)
__CPROVER_requires(__CPROVER_is_fresh(pcm, sizeof(mp3d_sample_t) * 64))
__CPROVER_requires(__CPROVER_is_fresh(z, sizeof(float) * 960))
__CPROVER_assigns(pcm[0], pcm[16 * nch])
{
    float a;
    a = (z[14 * 64] - z[0]) * 29;
    a += (z[1 * 64] + z[13 * 64]) * 213;
    a += (z[12 * 64] - z[2 * 64]) * 459;
    a += (z[3 * 64] + z[11 * 64]) * 2037;
    a += (z[10 * 64] - z[4 * 64]) * 5153;
    a += (z[5 * 64] + z[9 * 64]) * 6574;
    a += (z[8 * 64] - z[6 * 64]) * 37489;
    a += z[7 * 64] * 75038;
    pcm[0] = mp3d_scale_pcm(a);
    z += 2;
    a = z[14 * 64] * 104;
    a += z[12 * 64] * 1567;
    a += z[10 * 64] * 9727;
    a += z[8 * 64] * 64019;
    a += z[6 * 64] * -9975;
    a += z[4 * 64] * -45;
    a += z[2 * 64] * 146;
    a += z[0 * 64] * -5;
    pcm[16 * nch] = mp3d_scale_pcm(a);
}
