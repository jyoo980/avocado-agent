"""Tests for `tools.util.mutation`."""

from pathlib import Path

from tools.util.mutation import MutationScore, generate_mutants_and_compute_score


def test_mutation_testing_leaves_no_per_mutant_files(isolated_inc_source: Path) -> None:
    # The `isolated_inc_source` fixture checks for leftover files.
    result = generate_mutants_and_compute_score(str(isolated_inc_source), "inc")
    assert isinstance(result, MutationScore) and result.num_mutants > 0
