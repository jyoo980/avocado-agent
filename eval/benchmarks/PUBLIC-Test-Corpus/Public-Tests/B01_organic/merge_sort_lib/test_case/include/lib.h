typedef struct spritebatch_sprite_t {
    unsigned long long texture_id;
    int sort_bits;
} spritebatch_sprite_t;

void merge_sort(spritebatch_sprite_t *a, spritebatch_sprite_t *b, int size);
