#include <stdint.h>

typedef struct cn_rnd_t {
    uint64_t state[2];
} cn_rnd_t;

double next_double(cn_rnd_t *rnd);
