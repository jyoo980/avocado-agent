"""Tests for tools/mutate.py — pure mutation enumeration, no CBMC."""

from __future__ import annotations

from pathlib import Path

from tools.mutate import Mutant, enumerate_mutants, write_mutants_to_dir


def _write_c(tmp_path: Path, source: str) -> str:
    path = tmp_path / "fixture.c"
    path.write_text(source)
    return str(path)


def test_enumerate_mutants_emits_one_per_replacement(tmp_path: Path) -> None:
    file_path = _write_c(
        tmp_path,
        """
        int min(int a, int b)
        __CPROVER_requires(1)
        __CPROVER_ensures(__CPROVER_return_value <= a && __CPROVER_return_value <= b)
        {
            if (a < b) return a;
            return b;
        }
        """,
    )
    mutants = enumerate_mutants(file_path, "min")
    # `<` mutates to {<=, >, ==, !=} = 4 mutants from the body's single binary expression.
    relational = [m for m in mutants if m.operator_class == "ROR"]
    assert len(relational) == 4
    assert {m.replacement for m in relational} == {"<=", ">", "==", "!="}
    assert all(m.original == "<" for m in relational)


def test_mutants_preserve_contract_clauses(tmp_path: Path) -> None:
    """Mutating body operators must not perturb operators inside requires/ensures."""
    file_path = _write_c(
        tmp_path,
        """
        int absolute(int x)
        __CPROVER_requires(x > -1000 && x < 1000)
        __CPROVER_ensures(__CPROVER_return_value >= 0)
        {
            if (x < 0) return -x;
            return x;
        }
        """,
    )
    mutants = enumerate_mutants(file_path, "absolute")
    for m in mutants:
        # The original requires-clause text must appear unchanged in every mutant.
        assert "x > -1000 && x < 1000" in m.mutant_source
        # The original ensures-clause text must appear unchanged in every mutant.
        assert "__CPROVER_return_value >= 0" in m.mutant_source


def test_mutants_change_exactly_one_byte_range(tmp_path: Path) -> None:
    file_path = _write_c(
        tmp_path,
        """
        int add(int a, int b)
        __CPROVER_ensures(__CPROVER_return_value == a + b)
        {
            return a + b;
        }
        """,
    )
    original = Path(file_path).read_text()
    mutants = enumerate_mutants(file_path, "add")
    aor = [m for m in mutants if m.operator_class == "AOR"]
    assert aor, "expected at least one arithmetic mutant"
    for m in aor:
        # Reconstruct: original with one slice replaced should equal mutant_source.
        prefix = original.encode()[: m.start_byte].decode()
        suffix = original.encode()[m.end_byte :].decode()
        assert m.mutant_source == prefix + m.replacement + suffix


def test_unknown_function_yields_empty_list(tmp_path: Path) -> None:
    file_path = _write_c(
        tmp_path,
        """
        int present(int a)
        __CPROVER_ensures(__CPROVER_return_value == a)
        { return a; }
        """,
    )
    assert enumerate_mutants(file_path, "absent") == []


def test_no_mutatable_operators_yields_empty(tmp_path: Path) -> None:
    file_path = _write_c(
        tmp_path,
        """
        int passthrough(int a)
        __CPROVER_ensures(__CPROVER_return_value == a)
        { return a; }
        """,
    )
    assert enumerate_mutants(file_path, "passthrough") == []


def test_conditional_operators_are_mutated(tmp_path: Path) -> None:
    file_path = _write_c(
        tmp_path,
        """
        int both(int a, int b)
        __CPROVER_ensures(__CPROVER_return_value == 0 || __CPROVER_return_value == 1)
        {
            if (a > 0 && b > 0) return 1;
            return 0;
        }
        """,
    )
    cor = [m for m in enumerate_mutants(file_path, "both") if m.operator_class == "COR"]
    # `&&` in the body has one replacement (`||`); the spec's `||` is outside the body.
    assert len(cor) == 1
    assert cor[0].original == "&&"
    assert cor[0].replacement == "||"


def test_write_mutants_to_dir_creates_one_file_per_mutant(tmp_path: Path) -> None:
    file_path = _write_c(
        tmp_path,
        """
        int sub(int a, int b)
        __CPROVER_ensures(__CPROVER_return_value == a - b)
        { return a - b; }
        """,
    )
    mutants = enumerate_mutants(file_path, "sub")
    out_dir = tmp_path / "mutants"
    paths = write_mutants_to_dir(mutants, out_dir)
    assert len(paths) == len(mutants)
    assert all(p.exists() for p in paths)
    # Each file's content matches the corresponding mutant's source.
    for m, p in zip(mutants, paths, strict=True):
        assert p.read_text() == m.mutant_source


def test_mutant_only_touches_the_requested_function(tmp_path: Path) -> None:
    file_path = _write_c(
        tmp_path,
        """
        int target(int a, int b)
        __CPROVER_ensures(__CPROVER_return_value == a + b)
        {
            return a + b;
        }

        int neighbor(int x, int y)
        __CPROVER_ensures(__CPROVER_return_value == x - y)
        {
            return x - y;
        }
        """,
    )
    for m in enumerate_mutants(file_path, "target"):
        # The neighbor function's body, including its `x - y`, must remain intact.
        assert "return x - y;" in m.mutant_source


def test_mutants_can_be_re_enumerated_for_each_call(tmp_path: Path) -> None:
    """Two calls return equivalent mutant sets — no parser state leaks between calls."""
    file_path = _write_c(
        tmp_path,
        """
        int g(int a, int b)
        __CPROVER_ensures(__CPROVER_return_value == a + b)
        { return a + b; }
        """,
    )
    first = enumerate_mutants(file_path, "g")
    second = enumerate_mutants(file_path, "g")
    assert [m.mutant_source for m in first] == [m.mutant_source for m in second]
    assert all(isinstance(m, Mutant) for m in first)
