"""Tests for the stub index and in-file caller/callee helpers in `tools.util`."""

from pathlib import Path

import pytest

from tools.util import build_stub_index, get_in_file_callers_of
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


def test_build_stub_index_allows_repeated_marker_in_one_file(tmp_path: Path) -> None:
    stub = tmp_path / "a.c"
    stub.write_text("/* FUNCTION: foo */\nint foo(void);\n/* FUNCTION: foo */\n")

    assert build_stub_index(tmp_path) == {"foo": stub}


def test_build_stub_index_rejects_function_modeled_in_two_files(tmp_path: Path) -> None:
    (tmp_path / "a.c").write_text("/* FUNCTION: foo */\n")
    (tmp_path / "b.c").write_text("/* FUNCTION: foo */\n")

    with pytest.raises(ValueError, match=r"'foo' is modeled by both .*a\.c and .*b\.c"):
        build_stub_index(tmp_path)
