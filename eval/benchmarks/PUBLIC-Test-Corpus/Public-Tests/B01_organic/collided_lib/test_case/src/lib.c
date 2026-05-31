#include "lib.h"

typedef struct c2v {
    float x;
    float y;
} c2v;

typedef struct c2Circle {
    c2v p;
    float r;
} c2Circle;

typedef struct c2AABB {
    c2v min;
    c2v max;
} c2AABB;

c2v c2V(float x, float y) {
    c2v a;
    a.x = x;
    a.y = y;
    return a;
}

c2v c2Maxv(c2v a, c2v b) {
    return c2V(((a.x) > (b.x) ? (a.x) : (b.x)),
               ((a.y) > (b.y) ? (a.y) : (b.y)));
}

c2v c2Minv(c2v a, c2v b) {
    return c2V(((a.x) < (b.x) ? (a.x) : (b.x)),
               ((a.y) < (b.y) ? (a.y) : (b.y)));
}

c2v c2Clampv(c2v a, c2v lo, c2v hi) {
    return c2Maxv(lo, c2Minv(a, hi));
}

c2v c2Sub(c2v a, c2v b) {
    a.x -= b.x;
    a.y -= b.y;
    return a;
}

float c2Dot(c2v a, c2v b) {
    return a.x * b.x + a.y * b.y;
}

int c2CircletoCircle(c2Circle A, c2Circle B) {
    c2v c = c2Sub(B.p, A.p);
    float d2 = c2Dot(c, c);
    float r2 = A.r + B.r;
    r2 = r2 * r2;
    return d2 < r2;
}

int c2CircletoAABB(c2Circle A, c2AABB B) {
    c2v L = c2Clampv(A.p, B.min, B.max);
    c2v ab = c2Sub(A.p, L);
    float d2 = c2Dot(ab, ab);
    float r2 = A.r * A.r;
    return d2 < r2;
}

int c2AABBtoAABB(c2AABB A, c2AABB B) {
    int d0 = B.max.x < A.min.x;
    int d1 = A.max.x < B.min.x;
    int d2 = B.max.y < A.min.y;
    int d3 = A.max.y < B.min.y;
    return !(d0 | d1 | d2 | d3);
}

int collided(const void *A, C2_TYPE typeA, const void *B, C2_TYPE typeB)
    /* A and B are cast to either c2Circle or c2AABB; c2AABB (4 floats) is the
       larger, so each operand must be fresh for at least that many bytes. */
    __CPROVER_requires(__CPROVER_is_fresh(A, sizeof(c2AABB)) &&
                       __CPROVER_is_fresh(B, sizeof(c2AABB)))
    __CPROVER_assigns()
{
    switch (typeA) {
    case C2_TYPE_CIRCLE:
        switch (typeB) {
        case C2_TYPE_CIRCLE:
            return c2CircletoCircle(*(c2Circle *)A, *(c2Circle *)B);
        case C2_TYPE_AABB:
            return c2CircletoAABB(*(c2Circle *)A, *(c2AABB *)B);
        default:
            return 0;
        }
        break;
    case C2_TYPE_AABB:
        switch (typeB) {
        case C2_TYPE_CIRCLE:
            return c2CircletoAABB(*(c2Circle *)B, *(c2AABB *)A);
        case C2_TYPE_AABB:
            return c2AABBtoAABB(*(c2AABB *)A, *(c2AABB *)B);
        default:
            return 0;
        }
        break;
    default:
        return 0;
    }
}

#ifdef CBMC_HARNESS
void collided_harness(void) {
    const void *A;
    const void *B;
    C2_TYPE typeA, typeB;
    collided(A, typeA, B, typeB);
}
#endif
