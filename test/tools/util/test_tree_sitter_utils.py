"""Tests for tree_sitter utility functions."""

from tools.util import tree_sitter_utils


def test_construct_call_graph_only_leaf_nodes() -> None:
    call_graph = tree_sitter_utils.get_call_graph("test/data/no_callees.c")
    assert len(call_graph) == 3, (
        f"Expected 3 functions in simple.c, but got {len(call_graph)}"
    )
    functions = ["foo", "bar", "baz"]
    for f in functions:
        assert call_graph[f] == [], (
            f"Expected '{f}' in simple.c to have no callees, but got {call_graph[f]}"
        )


def test_construct_call_graph_with_callees_no_libraries() -> None:
    call_graph = tree_sitter_utils.get_call_graph("test/data/quicksort.c")
    assert len(call_graph) == 3, (
        f"Expected 3 functions in simple.c, but got {len(call_graph)}"
    )
    assert call_graph == {
        "swap": [],
        "quickSort": ["quickSort", "partition"],
        "partition": ["swap"],
    }
