#include "lib.h"

float ldexp_q2(float y, int exp_q2)
__CPROVER_requires(exp_q2 >= 0)
__CPROVER_assigns()
{
    static const float g_expfrac[4] = {9.31322575e-10f, 7.83145814e-10f,
                                       6.58544508e-10f, 5.53767716e-10f};
    int e;
    do {
        e = ((30 * 4) > (exp_q2) ? (exp_q2) : (30 * 4));
        y *= g_expfrac[e & 3] * (1 << 30 >> (e >> 2));
    } while ((exp_q2 -= e) > 0);
    return y;
}
