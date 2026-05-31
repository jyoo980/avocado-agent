#include "lib.h"

static uint64_t cn_rnd_next(cn_rnd_t *rnd)
    __CPROVER_requires(__CPROVER_is_fresh(rnd, sizeof(*rnd)))
    __CPROVER_assigns(rnd->state[0], rnd->state[1])
    __CPROVER_ensures(rnd->state[0] == __CPROVER_old(rnd->state[1]))
{
    uint64_t x = rnd->state[0];
    uint64_t y = rnd->state[1];
    rnd->state[0] = y;
    x ^= x << 23;
    x ^= x >> 17;
    x ^= y ^ (y >> 26);
    rnd->state[1] = x;
    return x + y;
}

double next_double(cn_rnd_t *rnd)
    __CPROVER_requires(__CPROVER_is_fresh(rnd, sizeof(*rnd)))
    __CPROVER_assigns(rnd->state[0], rnd->state[1])
    __CPROVER_ensures(__CPROVER_return_value >= 0.0 && __CPROVER_return_value < 1.0)
{
    uint64_t value = cn_rnd_next(rnd);
    uint64_t exponent = 1023;
    uint64_t mantissa = value >> 12;
    uint64_t result = (exponent << 52) | mantissa;
    return *(double *)&result - 1.0;
}
