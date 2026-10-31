"""Tests for `tools.util.mutation`."""

from pathlib import Path

from tools.util.mutation import MutationScore, generate_mutants_and_compute_score


SOURCE = """int inc(int x)
__CPROVER_requires(0 <= x && x < 8)
__CPROVER_ensures(__CPROVER_return_value == x + 1)
{ return x + 1; }
"""


def test_mutation_testing_leaves_no_per_mutant_files(tmp_path: Path) -> None:
    source = tmp_path / "inc.c"
    source.write_text(SOURCE)
    result = generate_mutants_and_compute_score(str(source), "inc")
    assert isinstance(result, MutationScore) and result.num_mutants > 0
    # Each mutant's source and its `-cbmc-runs.jsonl` log are deleted with it.
    left = [path.name for path in tmp_path.iterdir() if "__mutant_" in path.name]
    assert not [name for name in left if name.endswith((".c", "-cbmc-runs.jsonl"))], left
