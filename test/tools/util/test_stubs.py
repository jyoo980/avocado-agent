"""Tests for stub-resolution helpers."""

import json
from pathlib import Path

from tools.util.stubs import (
    build_stub_index,
    get_in_file_callees_for,
    get_unstubbed_external_callees_for,
    get_stub_paths_for,
)


def test_build_stub_index_resolves_common_libc_names() -> None:
    index = build_stub_index()
    assert index["printf"].name == "stdio.c"
    assert index["malloc"].name == "stdlib.c"
    assert index["strcpy"].name == "string.c"


def test_get_stub_paths_for_external_callees(tmp_path: Path) -> None:
    call_graph = {
        "foo": {"internal": ["bar"], "external": ["printf", "malloc"]},
        "bar": {"internal": [], "external": ["strcpy"]},
    }
    index = build_stub_index()
    resolved = get_stub_paths_for("foo", call_graph, index)

    # Only `foo`'s direct externals are resolved; `strcpy` is only reachable via `bar`.
    names = sorted(Path(p).name for p in resolved)
    assert names == ["stdio.c", "stdlib.c"]


def test_get_stub_paths_for_unknown_callee_is_dropped(tmp_path: Path) -> None:
    call_graph = {"foo": {"internal": [], "external": ["nonexistent_libc_thing"]}}
    cg_path = tmp_path / "cg.json"
    cg_path.write_text(json.dumps(call_graph))

    assert get_stub_paths_for("foo", call_graph, build_stub_index()) == []


def test_get_in_file_callees_for_excludes_externals_and_self(tmp_path: Path) -> None:
    call_graph = {
        "quickSort": {"internal": ["quickSort", "partition"], "external": ["printf"]},
        "partition": {"internal": ["swap"], "external": []},
        "swap": {"internal": [], "external": []},
    }
    cg_path = tmp_path / "cg.json"
    cg_path.write_text(json.dumps(call_graph))

    assert get_in_file_callees_for("quickSort", call_graph) == ["partition"]
    assert get_in_file_callees_for("partition", call_graph) == ["swap"]
    assert get_in_file_callees_for("swap", call_graph) == []


def test_get_in_file_callees_for_include_self_keeps_recursive_callee(tmp_path: Path) -> None:
    call_graph = {
        "quickSort": {"internal": ["quickSort", "partition"], "external": ["printf"]},
        "partition": {"internal": ["swap"], "external": []},
    }
    cg_path = tmp_path / "cg.json"
    cg_path.write_text(json.dumps(call_graph))

    assert get_in_file_callees_for("quickSort", call_graph, include_self=True) == [
        "partition",
        "quickSort",
    ]
    # Non-recursive functions are unaffected by the flag.
    assert get_in_file_callees_for("partition", call_graph, include_self=True) == ["swap"]


def test_get_unstubbed_external_callees_for_returns_only_unmodeled(tmp_path: Path) -> None:
    call_graph = {
        "foo": {
            "internal": ["bar"],
            "external": ["printf", "malloc", "some_project_helper"],
        },
    }
    cg_path = tmp_path / "cg.json"
    cg_path.write_text(json.dumps(call_graph))

    assert get_unstubbed_external_callees_for("foo", call_graph, build_stub_index()) == [
        "some_project_helper"
    ]


def test_get_unstubbed_external_callees_for_empty_when_all_stubbed(tmp_path: Path) -> None:
    call_graph = {"foo": {"internal": [], "external": ["printf", "malloc", "strcpy"]}}
    cg_path = tmp_path / "cg.json"
    cg_path.write_text(json.dumps(call_graph))

    assert get_unstubbed_external_callees_for("foo", call_graph, build_stub_index()) == []
