#include "lib.h"

static lm_vec2 lm_v2(float x, float y)
    __CPROVER_assigns()
{
    lm_vec2 v = {x, y};
    return v;
}

static lm_vec2 lm_sub2(lm_vec2 a, lm_vec2 b)
    __CPROVER_assigns()
{
    return lm_v2(a.x - b.x, a.y - b.y);
}

static float lm_dot2(lm_vec2 a, lm_vec2 b)
    __CPROVER_assigns()
{
    return a.x * b.x + a.y * b.y;
}

lm_vec2 to_barycentric(lm_vec2 p1, lm_vec2 p2, lm_vec2 p3, lm_vec2 p)
    __CPROVER_assigns()
{
    lm_vec2 v0 = lm_sub2(p3, p1);
    lm_vec2 v1 = lm_sub2(p2, p1);
    lm_vec2 v2 = lm_sub2(p, p1);
    float dot00 = lm_dot2(v0, v0);
    float dot01 = lm_dot2(v0, v1);
    float dot02 = lm_dot2(v0, v2);
    float dot11 = lm_dot2(v1, v1);
    float dot12 = lm_dot2(v1, v2);
    float invDenom = 1.0f / (dot00 * dot11 - dot01 * dot01);
    float u = (dot11 * dot02 - dot01 * dot12) * invDenom;
    float v = (dot00 * dot12 - dot01 * dot02) * invDenom;
    return lm_v2(u, v);
}
