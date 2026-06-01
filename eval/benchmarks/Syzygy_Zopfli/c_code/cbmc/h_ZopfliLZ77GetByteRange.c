#include "zopfli.c"
void harness(void) { ZopfliLZ77Store s; size_t a, b; ZopfliLZ77GetByteRange(&s, a, b); }
