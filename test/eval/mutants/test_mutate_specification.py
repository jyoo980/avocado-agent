"""Regression tests for mutate_specification on files that tree-sitter mis-parses."""

from eval.mutants.mutate_specification import CbmcClause, get_clause_mutants


_BENCHMARK = "test/quicksort/quicksort.c"


def test_partition_clauses_are_attributed_to_partition_not_quicksort() -> None:
    mutants = get_clause_mutants(_BENCHMARK, "partition")

    # Partition has 2 requires, 1 assigns, 4 ensures (two of which are forall predicates).
    kinds = [m.clause_kind for m in mutants]
    assert kinds == [
        CbmcClause.REQUIRES,
        CbmcClause.REQUIRES,
        CbmcClause.ASSIGNS,
        CbmcClause.ENSURES,
        CbmcClause.ENSURES,
        CbmcClause.ENSURES,
        CbmcClause.ENSURES,
    ]

    # Every clause must live on a line inside the partition contract block (lines 19-33).
    for m in mutants:
        assert 19 <= m.line <= 33, m


def test_partition_forall_clause_is_captured_in_full() -> None:
    mutants = get_clause_mutants(_BENCHMARK, "partition")
    forall_clauses = [m for m in mutants if "__CPROVER_forall" in m.clause_text]
    assert len(forall_clauses) == 2
    for m in forall_clauses:
        # Multi-line forall clauses must not be clipped after the opening line.
        assert m.clause_text.endswith("})"), m.clause_text
        assert m.clause_text.count("\n") >= 3, m.clause_text


def test_quicksort_clauses_are_recovered() -> None:
    mutants = get_clause_mutants(_BENCHMARK, "quickSort")
    # quickSort has 2 requires, 1 assigns, 1 ensures (forall).
    kinds = [m.clause_kind for m in mutants]
    assert kinds == [
        CbmcClause.REQUIRES,
        CbmcClause.REQUIRES,
        CbmcClause.REQUIRES,
        CbmcClause.ASSIGNS,
        CbmcClause.ENSURES,
    ]
    for m in mutants:
        assert 54 <= m.line <= 62, m
