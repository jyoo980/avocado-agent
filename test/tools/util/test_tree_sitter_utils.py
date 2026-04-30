"""Tests for tree_sitter utility functions."""

from tools.util import tree_sitter_utils


def test_construct_call_graph_only_leaf_nodes() -> None:
    call_graph = tree_sitter_utils.get_call_graph("test/data/no_callees.c")
    assert len(call_graph) == 3, (
        f"Expected 3 functions in simple.c, but got {len(call_graph)}"
    )
    functions = ["foo", "bar", "baz"]
    for f in functions:
        assert call_graph[f] == {"internal": [], "external": []}, (
            f"Expected '{f}' in simple.c to have no callees, but got {call_graph[f]}"
        )


def test_construct_call_graph_with_callees_no_libraries() -> None:
    call_graph = tree_sitter_utils.get_call_graph("test/data/quicksort.c")
    assert len(call_graph) == 3, (
        f"Expected 3 functions in quicksort.c, but got {len(call_graph)}"
    )
    assert call_graph["swap"] == {"internal": [], "external": []}, (
        f"Expected 'swap' in quicksort.c' to have no callees, but got {call_graph['swap']}"
    )
    assert call_graph["partition"]["internal"] == ["swap"], (
        f"Expected 'partition' in quicksort.c' to have internal callee 'swap'"
    )

    internal_callees_of_quicksort = call_graph["quickSort"]["internal"]
    for callee in ["quickSort", "partition"]:
        assert callee in internal_callees_of_quicksort, (
            f"Expected '{callee}' to be an internal callee of quickSort"
        )
