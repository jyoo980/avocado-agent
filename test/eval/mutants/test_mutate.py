"""Tests for mutate.py"""

from eval import get_mutants, MutationClasses, Mutant


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
