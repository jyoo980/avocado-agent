#include "zopfli.c"
void harness(void) { unsigned char a; size_t pos, end; ZopfliHash h; ZopfliWarmupHash(&a, pos, end, &h); }
