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
