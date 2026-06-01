#include <stddef.h>
#include <stdlib.h>

#include "lib.h"

char *bin2hex(char *hex, size_t hex_maxlen, const uint8_t *bin,
                    size_t bin_len)
__CPROVER_requires(bin_len < 8)
__CPROVER_requires(hex_maxlen > bin_len * 2U)
__CPROVER_requires(__CPROVER_is_fresh(hex, hex_maxlen))
__CPROVER_requires(__CPROVER_is_fresh(bin, bin_len))
__CPROVER_assigns(__CPROVER_object_upto(hex, bin_len * 2U + 1U))
__CPROVER_ensures(__CPROVER_return_value == hex)
{
    size_t i = (size_t)0U;
    unsigned int x;
    int b;
    int c;
    if (bin_len >= (18446744073709551615UL) / 2 || hex_maxlen <= bin_len * 2U) {
        abort();
    }
    while (i < bin_len) {
        c = bin[i] & 0xf;
        b = bin[i] >> 4;
        x = (unsigned char)(87U + c + (((c - 10U) >> 8) & ~38U)) << 8 |
            (unsigned char)(87U + b + (((b - 10U) >> 8) & ~38U));
        hex[i * 2U] = (char)x;
        x >>= 8;
        hex[i * 2U + 1U] = (char)x;
        i++;
    }
    hex[i * 2U] = 0U;
    return hex;
}
