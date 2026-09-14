/* `#if 0` blocks must not contribute calls or definitions. */
static void reset(void) {}
static void live(void) {}
static void mid(void) {}
static void late(void) {}

static int hash(int x)
{
#if 0
    reset();
    return x;
#else
    live();
#endif
    /* A directive-looking line inside a comment must not start a dead branch:
#if 0
    */
    const char* s = "#if 0";
    (void)s;
#if 0
    reset();
#elif 1
    mid();
#else
    late();
#endif
    return x;
}

#if 0
static int dead_fn(void) { reset(); return 0; }
#endif
