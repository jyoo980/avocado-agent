"""Tests for tools/clause_mutate.py — pure clause-mutation enumeration."""

from __future__ import annotations

from pathlib import Path

from tools.clause_mutate import enumerate_clause_mutants


def _write_c(tmp_path: Path, source: str) -> str:
    path = tmp_path / "fixture.c"
    path.write_text(source)
    return str(path)


def test_one_mutant_per_clause(tmp_path: Path) -> None:
    file_path = _write_c(
        tmp_path,
        """
        int add(int a, int b)
        __CPROVER_requires(a >= 0)
        __CPROVER_requires(b >= 0)
        __CPROVER_ensures(__CPROVER_return_value == a + b)
        { return a + b; }
        """,
    )
    mutants = enumerate_clause_mutants(file_path, "add")
    assert len(mutants) == 3
    assert sorted(m.clause_kind for m in mutants) == ["ensures", "requires", "requires"]


def test_each_mutant_drops_exactly_one_clause(tmp_path: Path) -> None:
    file_path = _write_c(
        tmp_path,
        """
        int add(int a, int b)
        __CPROVER_requires(a >= 0)
        __CPROVER_requires(b >= 0)
        __CPROVER_ensures(__CPROVER_return_value == a + b)
        { return a + b; }
        """,
    )
    original = Path(file_path).read_text()
    mutants = enumerate_clause_mutants(file_path, "add")
    for mutant in mutants:
        # Mutant source equals the original with exactly one clause's bytes excised.
        assert mutant.clause_text in original
        assert mutant.clause_text not in mutant.mutant_source.replace("\n", " ").replace(
            "  ", " "
        ) or original.count(mutant.clause_text) > 1


def test_mutant_preserves_function_body(tmp_path: Path) -> None:
    file_path = _write_c(
        tmp_path,
        """
        int square(int x)
        __CPROVER_requires(x >= 0)
        __CPROVER_ensures(__CPROVER_return_value >= 0)
        { return x * x; }
        """,
    )
    for mutant in enumerate_clause_mutants(file_path, "square"):
        assert "return x * x;" in mutant.mutant_source


def test_unknown_function_yields_empty(tmp_path: Path) -> None:
    file_path = _write_c(
        tmp_path,
        """
        int present(int a)
        __CPROVER_requires(1)
        { return a; }
        """,
    )
    assert enumerate_clause_mutants(file_path, "absent") == []


def test_other_functions_clauses_are_not_dropped(tmp_path: Path) -> None:
    file_path = _write_c(
        tmp_path,
        """
        int target(int a)
        __CPROVER_requires(a > 0)
        __CPROVER_ensures(__CPROVER_return_value == a)
        { return a; }

        int neighbor(int b)
        __CPROVER_requires(b > 0)
        __CPROVER_ensures(__CPROVER_return_value == b)
        { return b; }
        """,
    )
    for mutant in enumerate_clause_mutants(file_path, "target"):
        assert "__CPROVER_requires(b > 0)" in mutant.mutant_source
        assert "__CPROVER_ensures(__CPROVER_return_value == b)" in mutant.mutant_source
