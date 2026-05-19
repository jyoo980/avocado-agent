"""Tests for mutate.py"""

from pathlib import Path

from eval import get_mutants, MutationClasses
from tools.util.cbmc_clause_stripper import strip_cbmc_clauses


def test_get_mutants_no_op() -> None:
    assert get_mutants("test/data/mutants/mutants.c", "main") == []


def test_get_mutants_arithmetic_operator() -> None:
    mutants = get_mutants("test/data/mutants/mutants.c", "add")
    assert len(mutants) == 1, (
        f"Expected one mutant (replacing '+' with '-'), but got: {mutants}"
    )

    mutant = mutants[0]
    assert mutant.operator_class == MutationClasses.ARITHMETIC
    assert mutant.replacement_operator == "-"


def test_get_mutants_relational_operator() -> None:
    mutants = get_mutants("test/data/mutants/mutants.c", "compare")
    expected_replacement_operators = {"<=", ">", ">=", "==", "!="}

    assert len(mutants) == len(expected_replacement_operators), (
        f"Expected {len(expected_replacement_operators)} mutants, but got {len(mutants)}"
    )
    assert {
        mutant.replacement_operator for mutant in mutants
    } == expected_replacement_operators


def test_get_mutants_relational_compare() -> None:
    mutants = get_mutants("test/data/mutants/mutants.c", "for_loop")
    expected_replacement_operators = {"<=", ">", ">=", "==", "!=", "-"}

    # The for-loop has a comparison (leading to 6 mutants)
    # The body of the for-loop has an add (leading to 1 mutant)
    # There should be at total of 7 mutants
    assert len(mutants) == len(expected_replacement_operators), (
        f"Expected {len(expected_replacement_operators)} mutants, but got {len(mutants)}"
    )
    assert {
        mutant.replacement_operator for mutant in mutants
    } == expected_replacement_operators


def test_get_mutants_with_cbmc_annotated_function() -> None:
    mutants = get_mutants("test/data/mutants/mutants.c", "partition")

    assert len(mutants) == 14, f"Expected 14 mutants, but got {len(mutants)}"

    expected_replacement_operators = {">", ">=", "<", "==", "!=", "-", "+"}
    assert {
        mutant.replacement_operator for mutant in mutants
    } == expected_replacement_operators


def test_mutant_get_diff() -> None:
    mutants = get_mutants("test/data/mutants/mutants.c", "add")
    assert len(mutants) == 1
    diff = mutants[0].get_unified_diff()

    assert "--- original" in diff
    assert "+++ mutant" in diff
    assert "-    return a + b;" in diff
    assert "+    return a - b;" in diff

    # No context lines: only the headers, the hunk marker, and one -/+ pair.
    context_lines = [line for line in diff.splitlines() if line.startswith(" ")]
    assert context_lines == []

def test_get_mutants_recovers_function_after_forall_annotated_neighbor() -> None:
    # Regression: `quickSort` in eval/benchmarks/quicksort/quicksort.c sits after a `partition`
    # function whose `__CPROVER_forall { ... }` clauses make tree-sitter mis-parse. Previously
    # the iterator missed `quickSort` entirely and `get_mutants` raised ValueError.
    benchmark = "eval/benchmarks/quicksort/quicksort.c"
    mutants = get_mutants(benchmark, "quickSort")
    assert mutants, "Expected non-empty mutants for quickSort"

    # Every mutated operator must lie inside the function body, never inside a CBMC clause.
    source = Path(benchmark).read_bytes()
    _, spans = strip_cbmc_clauses(source)
    for m in mutants:
        for span in spans:
            assert not (
                span.start_byte <= m.start_byte and m.end_byte <= span.end_byte
            ), f"Mutant at byte {m.start_byte} lies inside CBMC clause span {span}"
