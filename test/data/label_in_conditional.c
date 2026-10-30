/* Preprocessor conditionals inside a function body that wrap a label or split a statement,
 * as in LZ4_decompress_generic (lz4.c) and XXH32_update_endian (xxhash.c). */
static int decode(const char* p, int fast)
{
    int n = 0;
    if (p == 0)
#if ACCEPT_NULL
        return 0;
#else
        return -1;
#endif
    if (fast) { goto safe_copy; }
    n += 1;
#if FAST_LOOP
safe_copy:
#endif
    n += 2;
    return n;
}

static int decode_all(const char* p)
{
    return decode(p, 1) + decode(p, 0);
}
