#include "lib.h"

int div_euclid(int v1, int v2)
    __CPROVER_requires(!(v1 == (-0x7fffffff - 1) && v2 == -1))
    __CPROVER_assigns()
{
    if (v2 == 0) {
        return 0;
    }
    int q, r;
    if (v1 >= 0)
        if (v2 >= 0)
            return ((v1) / (v2));
        else if (v2 != (-0x7fffffff - 1))
            q = -((v1) / (-v2)), r = ((v1) % (-v2));
        else
            q = 0, r = v1;
    else if (v1 != (-0x7fffffff - 1))
        if (v2 >= 0)
            q = -((-v1) / (v2)), r = -((-v1) % (v2));
        else if (v2 != (-0x7fffffff - 1))
            q = ((-v1) / (-v2)), r = -((-v1) % (-v2));
        else
            q = 1, r = v1 - q * v2;
    else if (v2 >= 0)
        q = -((-(v1 + v2)) / (v2)) - 1, r = -((-(v1 + v2)) % (v2));
    else if (v2 != (-0x7fffffff - 1))
        q = ((-(v1 - v2)) / (-v2)) + 1, r = -((-(v1 - v2)) % (-v2));
    else
        q = 1, r = 0;
    if (r >= 0)
        return q;
    else
        return q + (v2 > 0 ? -1 : 1);
}
