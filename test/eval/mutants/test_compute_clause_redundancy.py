"""Tests for `eval.mutants.compute_clause_redundancy`."""

from pathlib import Path

from eval.mutants.compute_clause_redundancy import compute_clause_redundancy_score


SOURCE = """int inc(int x)
__CPROVER_requires(0 <= x && x < 8)
__CPROVER_ensures(__CPROVER_return_value == x + 1)
{ return x + 1; }
"""


def test_clause_redundancy_scoring_leaves_no_per_mutant_files(tmp_path: Path) -> None:
    source = tmp_path / "inc.c"
    source.write_text(SOURCE)
    assert compute_clause_redundancy_score(str(source), "inc") is not None
    # Each removed-clause source and its `-cbmc-runs.jsonl` log are deleted with it.
    left = [path.name for path in tmp_path.iterdir() if "__clause_drop_" in path.name]
    assert not [name for name in left if name.endswith((".c", "-cbmc-runs.jsonl"))], left
