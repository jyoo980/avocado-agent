typedef enum {
    C2_TYPE_CIRCLE,
    C2_TYPE_AABB,
} C2_TYPE;

int collided(const void *A, C2_TYPE typeA, const void *B, C2_TYPE typeB);
