#include <string.h>

#include "lib.h"

int hex2bin(uint8_t *bin, size_t bin_maxlen, const char *hex,
                  size_t hex_len, const char *ignore, const char **hex_end_p)
    /* Bound hex_len/bin_maxlen so the loop is finitely unwound; hex is read at
       hex[0..hex_len-1] and bin written at bin[0..bin_maxlen-1]. ignore is held
       NULL because strchr would otherwise scan an unbounded string; hex_end_p
       is a fresh out-pointer that may receive &hex[hex_pos]. */
    __CPROVER_requires(hex_len <= 8 && bin_maxlen <= 8 && ignore == ((void *)0) &&
                       __CPROVER_is_fresh(hex, 8) &&
                       __CPROVER_is_fresh(bin, 8) &&
                       __CPROVER_is_fresh(hex_end_p, sizeof(const char *)))
    __CPROVER_assigns(__CPROVER_object_whole(bin), *hex_end_p)
{
    size_t bin_pos = (size_t)0U;
    size_t hex_pos = (size_t)0U;
    int ret = 0;
    unsigned char c;
    unsigned char c_alpha0, c_alpha;
    unsigned char c_num0, c_num;
    uint8_t c_acc = 0U;
    uint8_t c_val;
    unsigned char state = 0U;
    while (hex_pos < hex_len) {
        c = (unsigned char)hex[hex_pos];
        c_num = c ^ 48U;
        c_num0 = (c_num - 10U) >> 8;
        c_alpha = (c & ~32U) - 55U;
        c_alpha0 = ((c_alpha - 10U) ^ (c_alpha - 16U)) >> 8;
        if ((c_num0 | c_alpha0) == 0U) {
            if (ignore != ((void *)0) && state == 0U &&
                strchr(ignore, c) != ((void *)0)) {
                hex_pos++;
                continue;
            }
            break;
        }
        c_val = (uint8_t)((c_num0 & c_num) | (c_alpha0 & c_alpha));
        if (bin_pos >= bin_maxlen) {
            ret = -1;
            break;
        }
        if (state == 0U) {
            c_acc = c_val * 16U;
        } else {
            bin[bin_pos++] = c_acc | c_val;
        }
        state = ~state;
        hex_pos++;
    }
    if (state != 0U) {
        hex_pos--;
        ret = -1;
    }
    if (ret != 0) {
        bin_pos = (size_t)0U;
    }
    if (hex_end_p != ((void *)0)) {
        *hex_end_p = &hex[hex_pos];
    } else if (hex_pos != hex_len) {
        ret = -1;
    }
    if (ret != 0) {
        return ret;
    }
    return (int)bin_pos;
}

#ifdef CBMC_HARNESS
void hex2bin_harness(void) {
    uint8_t *bin;
    size_t bin_maxlen;
    const char *hex;
    size_t hex_len;
    const char *ignore;
    const char **hex_end_p;
    hex2bin(bin, bin_maxlen, hex, hex_len, ignore, hex_end_p);
}
#endif
