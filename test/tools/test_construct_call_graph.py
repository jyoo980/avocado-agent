"""Tests for the call-graph cache in `tools.construct_call_graph`."""

import json
import os
from pathlib import Path

from tools.construct_call_graph import construct_call_graph

_SOURCE_A = "int a(void) { return 0; }\n"
_SOURCE_B = "int b(void) { return 0; }\n"


def _functions(path_to_call_graph: str) -> set[str]:
    return set(json.loads(Path(path_to_call_graph).read_text(encoding="utf-8")))


def _make_older(path: Path, seconds: int = 10) -> None:
    """Set `path`'s mtime to `seconds` in the past, without following symlinks."""
    stat = path.lstat()
    os.utime(
        path,
        ns=(stat.st_atime_ns, stat.st_mtime_ns - seconds * 10**9),
        follow_symlinks=False,
    )


def test_cache_reused_when_fresh(tmp_path: Path) -> None:
    source = tmp_path / "prog.c"
    source.write_text(_SOURCE_A, encoding="utf-8")
    _make_older(source)
    path_to_call_graph = construct_call_graph(str(source))
    Path(path_to_call_graph).write_text(json.dumps({"sentinel": {}}), encoding="utf-8")
    assert _functions(construct_call_graph(str(source))) == {"sentinel"}


def test_cache_regenerated_after_source_edit(tmp_path: Path) -> None:
    source = tmp_path / "prog.c"
    source.write_text(_SOURCE_A, encoding="utf-8")
    path_to_call_graph = construct_call_graph(str(source))
    assert _functions(path_to_call_graph) == {"a"}
    _make_older(Path(path_to_call_graph))
    source.write_text(_SOURCE_B, encoding="utf-8")
    assert _functions(construct_call_graph(str(source))) == {"b"}


def test_cache_regenerated_after_symlink_repointed(tmp_path: Path) -> None:
    target_a = tmp_path / "targets" / "a.c"
    target_b = tmp_path / "targets" / "b.c"
    target_a.parent.mkdir()
    target_a.write_text(_SOURCE_A, encoding="utf-8")
    target_b.write_text(_SOURCE_B, encoding="utf-8")
    _make_older(target_a, seconds=100)
    _make_older(target_b, seconds=100)

    link = tmp_path / "prog.c"
    link.symlink_to(target_a)
    _make_older(link, seconds=50)
    path_to_call_graph = construct_call_graph(str(link))
    assert Path(path_to_call_graph).parent == tmp_path  # Cached beside the link.
    assert _functions(path_to_call_graph) == {"a"}

    _make_older(Path(path_to_call_graph), seconds=20)
    link.unlink()
    # Target b is older than the cache; only the link's own (new) mtime reveals the change.
    link.symlink_to(target_b)
    assert _functions(construct_call_graph(str(link))) == {"b"}
