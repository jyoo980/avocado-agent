"""Tests for tree_sitter utility functions."""

from tools.util import tree_sitter_utils
from tools.util import CallGraphCallees


def test_construct_call_graph_only_leaf_nodes() -> None:
    call_graph = tree_sitter_utils.get_call_graph("test/data/no_callees.c")
    assert call_graph.size() == 3, (
        f"Expected 3 functions in simple.c, but got {call_graph.size()}"
    )
    functions = ["foo", "bar", "baz"]
    for f in functions:
        assert call_graph.get_callees(f) == CallGraphCallees(
            internal=[], external=[]
        ), (
            f"Expected '{f}' in simple.c to have no callees, but got {call_graph.get_callees(f)}"
        )


def test_construct_call_graph_with_callees_no_libraries() -> None:
    call_graph = tree_sitter_utils.get_call_graph("test/data/quicksort.c")
    assert call_graph.size() == 3, (
        f"Expected 3 functions in quicksort.c, but got {call_graph.size()}"
    )
    assert call_graph.get_callees("swap") == CallGraphCallees(
        internal=[], external=[]
    ), (
        f"Expected 'swap' in quicksort.c' to have no callees, but got {call_graph.get_callees('swap')}"
    )
    partition_callees = call_graph.get_callees("partition")
    assert partition_callees.internal == ["swap"], (
        f"Expected 'partition' in quicksort.c' to have internal callee 'swap'"
    )

    quicksort_callees = call_graph.get_callees("quickSort")
    for callee in ["quickSort", "partition"]:
        assert callee in quicksort_callees.internal, (
            f"Expected '{callee}' to be an internal callee of quickSort"
        )


def test_construct_call_graph_recovers_function_with_cprover_forall_clause() -> None:
    # `__CPROVER_forall { int k; ... }` braces confuse tree-sitter into wrapping the surrounding
    # function in an ERROR node rather than a function_definition. The call-graph build must
    # recover that ERROR-wrapped definition; otherwise the function disappears and its callers
    # mis-classify it as external.
    call_graph = tree_sitter_utils.get_call_graph("test/data/quicksort_with_forall.c")
    assert call_graph.size() == 3, (
        f"Expected 3 functions in quicksort_with_forall.c, but got {call_graph.size()}"
    )

    partition_callees = call_graph.get_callees("partition")
    assert partition_callees.internal == ["swap"], (
        f"Expected 'partition' to have internal callee 'swap', got {partition_callees}"
    )

    quicksort_callees = call_graph.get_callees("quickSort")
    for callee in ["quickSort", "partition"]:
        assert callee in quicksort_callees.internal, (
            f"Expected '{callee}' to be an internal callee of quickSort, got {quicksort_callees}"
        )
    assert "partition" not in quicksort_callees.external, (
        "ERROR-wrapped 'partition' must not be mis-classified as external"
    )


def test_construct_call_graph_recovers_function_with_cprover_forall_and_subscript_clause() -> (
    None
):
    # Subscript syntax inside a `__CPROVER_forall` clause (e.g., `arr[k]`) nests the real
    # `function_declarator` under one or more `array_declarator` wrappers inside the ERROR node,
    # rather than as a direct child. The call-graph build must still recover the function.
    call_graph = tree_sitter_utils.get_call_graph(
        "test/data/quicksort_with_forall_subscript.c"
    )
    assert call_graph.size() == 3, (
        f"Expected 3 functions in quicksort_with_forall_subscript.c, but got {call_graph.size()}"
    )

    partition_callees = call_graph.get_callees("partition")
    assert partition_callees.internal == ["swap"], (
        f"Expected 'partition' to have internal callee 'swap', got {partition_callees}"
    )

    quicksort_callees = call_graph.get_callees("quickSort")
    for callee in ["quickSort", "partition"]:
        assert callee in quicksort_callees.internal, (
            f"Expected '{callee}' to be an internal callee of quickSort, got {quicksort_callees}"
        )
    assert "partition" not in quicksort_callees.external, (
        "ERROR-wrapped 'partition' must not be mis-classified as external"
    )


def test_get_functions_with_cprover_annos() -> None:
    fns_with_annos = tree_sitter_utils.get_functions_with_cprover_annotations(
        "test/data/pointer_decl_function.c"
    )

    assert len(fns_with_annos) == 2, (
        f"Expected two functions with annotations, but got {len(fns_with_annos)}"
    )
    assert "swap" in fns_with_annos and "bin2hex" in fns_with_annos, (
        f"Expected 'swap' and 'bin2hex' in {fns_with_annos}"
    )
