#include <limits.h>

#define ARR_MAX 10

// A function whose *body* carries CBMC loop contracts. The `__CPROVER_forall { ... }`
// braces inside `__CPROVER_loop_invariant` previously closed the enclosing construct
// early when tree-sitter parsed the (only partially) stripped source, causing the body
// node to swallow `neighbor` below — so mutating `accumulate` produced mutants located
// inside `neighbor`.
int accumulate(int arr[], int n)
__CPROVER_requires(0 <= n && n <= ARR_MAX)
__CPROVER_assigns(__CPROVER_object_whole(arr))
__CPROVER_ensures(__CPROVER_return_value >= 0)
{
    int total = 0;

    for (int i = 0; i < n; i++)
    __CPROVER_assigns(i, total, __CPROVER_object_whole(arr))
    __CPROVER_loop_invariant(0 <= i && i <= n)
    __CPROVER_loop_invariant(__CPROVER_forall {
        int k;
        (0 <= k && k < ARR_MAX) ==> ((0 <= k && k < i) ==> arr[k] >= 0)
    })
    __CPROVER_decreases(n - i)
    {
        if (arr[i] >= 0) {
            total = total + arr[i];
        }
    }

    return total;
}

// The neighbor: its operators must never appear as mutants of `accumulate`.
int neighbor(int a, int b)
{
    if (a < b) {
        return a + b;
    }
    return a - b;
}
