"""Tests for the call-graph cache of `tools.construct_call_graph`."""

import json
import os
from pathlib import Path

from tools.construct_call_graph import construct_call_graph


def test_a_call_graph_older_than_its_source_is_rebuilt(tmp_path: Path) -> None:
    source = tmp_path / "f.c"
    source.write_text("int f(void) { return 0; }\n")
    path_to_call_graph = Path(construct_call_graph(str(source)))
    assert set(json.loads(path_to_call_graph.read_text())) == {"f"}

    # An agent adds a helper.  The cached call graph must not hide it.
    source.write_text("int g(void) { return 1; }\nint f(void) { return g(); }\n")
    cached = path_to_call_graph.stat().st_mtime_ns
    os.utime(source, ns=(cached + 1_000_000_000, cached + 1_000_000_000))
    assert construct_call_graph(str(source)) == str(path_to_call_graph)
    assert set(json.loads(path_to_call_graph.read_text())) == {"f", "g"}


def test_a_call_graph_newer_than_its_source_is_reused(tmp_path: Path) -> None:
    source = tmp_path / "f.c"
    source.write_text("int f(void) { return 0; }\n")
    path_to_call_graph = Path(construct_call_graph(str(source)))
    path_to_call_graph.write_text(json.dumps({"sentinel": {"internal": [], "external": []}}))
    written = source.stat().st_mtime_ns
    os.utime(path_to_call_graph, ns=(written + 1_000_000_000, written + 1_000_000_000))
    construct_call_graph(str(source))
    assert set(json.loads(path_to_call_graph.read_text())) == {"sentinel"}
