#include <stddef.h>
#include <stdlib.h>
#include <stdint.h>

void swap_no_annos(int *a, int *b)
{
    int t = *a;
    *a = *b;
    *b = t;
}

void swap(int* a, int* b)
__CPROVER_requires(__CPROVER_is_fresh(a, sizeof(int)))
__CPROVER_requires(__CPROVER_is_fresh(b, sizeof(int)))
__CPROVER_assigns(*a, *b)
__CPROVER_ensures(*a == __CPROVER_old(*b))
__CPROVER_ensures(*b == __CPROVER_old(*a))
{
    int t = *a;
    *a = *b;
    *b = t;
}

char *bin2hex(char *hex, size_t hex_maxlen, const uint8_t *bin,
                    size_t bin_len)
__CPROVER_requires(bin_len < 4)
__CPROVER_requires(hex_maxlen > bin_len * 2U)
__CPROVER_requires(hex_maxlen < 16)
__CPROVER_requires(__CPROVER_is_fresh(hex, hex_maxlen))
__CPROVER_requires((bin_len > 0) ==> __CPROVER_is_fresh(bin, bin_len))
__CPROVER_assigns(__CPROVER_object_upto(hex, bin_len * 2U + 1U))
__CPROVER_ensures(__CPROVER_return_value == hex)
{

    return malloc(sizeof(char*));
}
