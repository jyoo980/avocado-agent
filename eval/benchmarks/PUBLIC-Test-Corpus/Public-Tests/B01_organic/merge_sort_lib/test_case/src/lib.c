#include <string.h>

#include "lib.h"

static int spritebatch_internal_sprite_less_than_or_equal(spritebatch_sprite_t *a,
                                               spritebatch_sprite_t *b)
__CPROVER_requires(__CPROVER_is_fresh(a, sizeof(spritebatch_sprite_t)))
__CPROVER_requires(__CPROVER_is_fresh(b, sizeof(spritebatch_sprite_t)))
__CPROVER_assigns()
__CPROVER_ensures(__CPROVER_return_value == 0 || __CPROVER_return_value == 1)
__CPROVER_ensures((a->sort_bits <= b->sort_bits) ? (__CPROVER_return_value == 1) : (__CPROVER_return_value == 0))
{
    if (a->sort_bits <= b->sort_bits)
        return 1;
    if (a->sort_bits == b->sort_bits && a->texture_id <= b->texture_id)
        return 1;
    return 0;
}

static void spritebatch_internal_merge_sort_iteration(spritebatch_sprite_t *a, int lo,
                                               int split, int hi,
                                               spritebatch_sprite_t *b)
__CPROVER_requires(0 <= lo && lo <= split && split <= hi && hi <= 4)
__CPROVER_requires(__CPROVER_is_fresh(a, 4 * sizeof(spritebatch_sprite_t)))
__CPROVER_requires(__CPROVER_is_fresh(b, 4 * sizeof(spritebatch_sprite_t)))
__CPROVER_assigns(__CPROVER_object_whole(b))
{
    int i = lo, j = split;
    for (int k = lo; k < hi; k++) {
        if (i < split &&
            (j >= hi ||
             spritebatch_internal_sprite_less_than_or_equal(a + i, a + j))) {
            b[k] = a[i];
            i = i + 1;
        } else {
            b[k] = a[j];
            j = j + 1;
        }
    }
}

static void spritebatch_internal_merge_sort_recurse(spritebatch_sprite_t *b, int lo,
                                             int hi, spritebatch_sprite_t *a)
__CPROVER_requires(0 <= lo && lo <= hi && hi <= 4)
__CPROVER_requires(__CPROVER_is_fresh(a, 4 * sizeof(spritebatch_sprite_t)))
__CPROVER_requires(__CPROVER_is_fresh(b, 4 * sizeof(spritebatch_sprite_t)))
__CPROVER_assigns(__CPROVER_object_whole(a), __CPROVER_object_whole(b))
{
    if (hi - lo <= 1)
        return;
    int split = (lo + hi) / 2;
    spritebatch_internal_merge_sort_recurse(a, lo, split, b);
    spritebatch_internal_merge_sort_recurse(a, split, hi, b);
    spritebatch_internal_merge_sort_iteration(b, lo, split, hi, a);
}

void merge_sort(spritebatch_sprite_t *a,
                                     spritebatch_sprite_t *b, int size)
__CPROVER_requires(0 <= size && size <= 4)
__CPROVER_requires(__CPROVER_is_fresh(a, 4 * sizeof(spritebatch_sprite_t)))
__CPROVER_requires(__CPROVER_is_fresh(b, 4 * sizeof(spritebatch_sprite_t)))
__CPROVER_assigns(__CPROVER_object_whole(a), __CPROVER_object_whole(b))
{
    memcpy(b, a, sizeof(spritebatch_sprite_t) * size);
    spritebatch_internal_merge_sort_recurse(b, 0, size, a);
}
