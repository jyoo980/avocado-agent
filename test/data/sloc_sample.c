/* Fixture for SLOC counting: comments, blank lines, dead code and CBMC clauses must not count. */
#include <stdio.h>

// A one-line function: signature, brace, body and brace all on one line.
int one_liner(void) { return 1; }

int with_comments(int x)
{
    // A comment-only line.

    int y = x + 1; /* trailing comment on a code line */
    /*
     * A multi-line comment.
     */
    return y;
}

int with_dead_code(int x)
{
#if 0
    x = -x;
    x = x * 2;
#endif
    return x;
}

int with_contract(int x)
__CPROVER_requires(x < 100)
__CPROVER_ensures(__CPROVER_return_value == x + 1)
{
    return x + 1;
}

int *returns_pointer(int *p)
{
    puts("a string literal \
spanning two lines");
    return p;
}
