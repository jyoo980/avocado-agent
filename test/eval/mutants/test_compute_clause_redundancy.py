"""Tests for `eval.mutants.compute_clause_redundancy`."""

from pathlib import Path

from eval.mutants.compute_clause_redundancy import compute_clause_redundancy_score


def test_clause_redundancy_scoring_leaves_no_per_mutant_files(isolated_inc_source: Path) -> None:
    # The `isolated_inc_source` fixture checks for leftover files.
    assert compute_clause_redundancy_score(str(isolated_inc_source), "inc") is not None
