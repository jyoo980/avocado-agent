"""Tests for `tools.construct_call_graph`."""

import json
from pathlib import Path

from tools.construct_call_graph import construct_call_graph


def test_the_call_graph_reflects_edits_to_the_source(tmp_path: Path) -> None:
    source = tmp_path / "f.c"
    source.write_text("int f(void) { return 0; }\n")
    path_to_call_graph = Path(construct_call_graph(str(source)))
    assert set(json.loads(path_to_call_graph.read_text())) == {"f"}

    # An agent adds a helper.  The previously written call graph must not hide it.
    source.write_text("int g(void) { return 1; }\nint f(void) { return g(); }\n")
    assert construct_call_graph(str(source)) == str(path_to_call_graph)
    assert set(json.loads(path_to_call_graph.read_text())) == {"f", "g"}
