typedef struct lm_vec2 {
    float x, y;
} lm_vec2;

lm_vec2 to_barycentric(lm_vec2 p1, lm_vec2 p2, lm_vec2 p3, lm_vec2 p);
