"""Tests for in-file caller/callee helpers in `tools.util.stubs`."""

from tools.util import get_in_file_callers_of
from tools.util.callgraph import CallGraph


def test_get_in_file_callers_of_returns_sorted_unique_callers() -> None:
    call_graph = CallGraph({
        "alpha": {"internal": ["target"], "external": []},
        "beta": {"internal": ["target", "other"], "external": []},
        "gamma": {"internal": ["other"], "external": []},
        "target": {"internal": [], "external": []},
    })

    assert get_in_file_callers_of("target", call_graph) == ["alpha", "beta"]


def test_get_in_file_callers_of_no_callers() -> None:
    call_graph = CallGraph({
        "alpha": {"internal": [], "external": []},
        "target": {"internal": [], "external": []},
    })

    assert get_in_file_callers_of("target", call_graph) == []


def test_get_in_file_callers_of_excludes_self_recursion() -> None:
    call_graph = CallGraph({
        "target": {"internal": ["target"], "external": []},
        "alpha": {"internal": ["target"], "external": []},
    })

    assert get_in_file_callers_of("target", call_graph) == ["alpha"]
