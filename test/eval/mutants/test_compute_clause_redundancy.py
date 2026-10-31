"""Tests for `eval.mutants.compute_clause_redundancy`."""

from pathlib import Path

from eval.mutants.compute_clause_redundancy import compute_clause_redundancy_score

SOURCE = """int inc(int x)
__CPROVER_requires(0 <= x && x < 8)
__CPROVER_ensures(__CPROVER_return_value == x + 1)
{ return x + 1; }

int twice_inc(int x)
__CPROVER_requires(0 <= x && x < 4)
__CPROVER_ensures(__CPROVER_return_value == x + 2)
{ return inc(inc(x)); }
"""


def test_clause_redundancy_scoring_builds_no_call_graph_per_removed_clause(
    tmp_path: Path,
) -> None:
    source = tmp_path / "inc.c"
    source.write_text(SOURCE)
    assert compute_clause_redundancy_score(str(source), "inc") is not None
    # Every run shares the original source's call graph, so none is cached beside a
    # removed-clause source.
    assert [path.name for path in tmp_path.glob("*-callgraph.json")] == ["inc-callgraph.json"]
