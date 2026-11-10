#include <assert.h>

#define DEBUGLOG(l, ...) {}
#define NOTHING(x)

int with_assert_and_debuglog(int a, int b)
{
    assert(a < b);
    DEBUGLOG(5, "%d", a - b);
    return a + b;
}

int with_preprocessor_condition(int a, int b)
{
#if defined(FOO) && defined(BAR)
    a = 0;
#endif
    return a + b;
}

int with_cprover_ifdef(int a, int b)
{
#ifdef __CPROVER__
    int saved = a + 1;
#endif
    int r = a - b;
#ifdef __CPROVER__
    __CPROVER_assert(saved > a || saved < a, "snapshot");
#endif
    return r;
}

int with_cprover_ifndef_else(int a, int b)
{
    int r;
#ifndef __CPROVER__
    r = a + b;
#else
    r = a - b;
#endif
    return r;
}

int with_macro_that_expands_to_nothing(int a, int b)
{
    NOTHING(a < b);
    return a + b;
}
