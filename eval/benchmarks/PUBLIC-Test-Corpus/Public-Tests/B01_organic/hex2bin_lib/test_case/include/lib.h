#include <stdint.h>
#include <stddef.h>

int hex2bin(uint8_t *bin, size_t bin_maxlen, const char *hex,
                  size_t hex_len, const char *ignore, const char **hex_end_p);
