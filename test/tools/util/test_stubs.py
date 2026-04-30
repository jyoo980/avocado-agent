"""Tests for stub-resolution helpers."""

import json
from pathlib import Path

from tools.util.stubs import build_stub_index, get_in_file_callees_for, resolve_stub_paths_for


def test_build_stub_index_resolves_common_libc_names() -> None:
    index = build_stub_index()
    assert index["printf"].name == "stdio.c"
    assert index["malloc"].name == "stdlib.c"
    assert index["strcpy"].name == "string.c"


def test_resolve_stub_paths_for_external_callees(tmp_path: Path) -> None:
    call_graph = {
        "foo": ["bar", "printf", "malloc"],
        "bar": ["strcpy"],
    }
    cg_path = tmp_path / "cg.json"
    cg_path.write_text(json.dumps(call_graph))

    index = build_stub_index()
    resolved = resolve_stub_paths_for("foo", str(cg_path), index)

    # `bar` is in-file (a key in the call graph) and is excluded; `strcpy` is reached only via
    # `bar` and is excluded since we only resolve direct callees of `foo`.
    names = sorted(Path(p).name for p in resolved)
    assert names == ["stdio.c", "stdlib.c"]


def test_resolve_stub_paths_for_unknown_callee_is_dropped(tmp_path: Path) -> None:
    call_graph = {"foo": ["nonexistent_libc_thing"]}
    cg_path = tmp_path / "cg.json"
    cg_path.write_text(json.dumps(call_graph))

    assert resolve_stub_paths_for("foo", str(cg_path), build_stub_index()) == []


def test_get_in_file_callees_for_excludes_externals_and_self(tmp_path: Path) -> None:
    call_graph = {
        "quickSort": ["quickSort", "partition", "printf"],
        "partition": ["swap"],
        "swap": [],
    }
    cg_path = tmp_path / "cg.json"
    cg_path.write_text(json.dumps(call_graph))

    assert get_in_file_callees_for("quickSort", str(cg_path)) == ["partition"]
    assert get_in_file_callees_for("partition", str(cg_path)) == ["swap"]
    assert get_in_file_callees_for("swap", str(cg_path)) == []
