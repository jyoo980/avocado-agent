#include <stddef.h>
#include <stdint.h>

char *bin2hex(char *hex, size_t hex_maxlen, const uint8_t *bin,
                    size_t bin_len);
