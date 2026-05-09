"""Tests for tools/spec_extract.py."""

from __future__ import annotations

from pathlib import Path

from tools.spec_extract import extract_function_spec


def _write_c(tmp_path: Path, source: str) -> str:
    path = tmp_path / "fixture.c"
    path.write_text(source)
    return str(path)


def test_extract_basic_spec(tmp_path: Path) -> None:
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
    spec = extract_function_spec(file_path, "add")
    assert spec is not None
    assert spec.name == "add"
    assert spec.return_type_text == "int"
    assert [p.name for p in spec.parameters] == ["a", "b"]
    assert spec.requires == ["a >= 0", "b >= 0"]
    assert spec.ensures == ["__CPROVER_return_value == a + b"]
    assert spec.assigns == []
    assert spec.frees == []


def test_extract_pointer_param_declarator(tmp_path: Path) -> None:
    file_path = _write_c(
        tmp_path,
        """
        void zero(int *p)
        __CPROVER_requires(__CPROVER_is_fresh(p, sizeof(int)))
        __CPROVER_assigns(*p)
        __CPROVER_ensures(*p == 0)
        { *p = 0; }
        """,
    )
    spec = extract_function_spec(file_path, "zero")
    assert spec is not None
    assert spec.parameters[0].name == "p"
    # Declarator text retains the pointer marker so the harness can re-emit it verbatim.
    assert "*p" in spec.parameters[0].declarator_text
    assert spec.assigns == ["*p"]


def test_returns_none_for_missing_function(tmp_path: Path) -> None:
    file_path = _write_c(
        tmp_path,
        """
        int present(int x)
        __CPROVER_requires(1)
        { return x; }
        """,
    )
    assert extract_function_spec(file_path, "absent") is None


def test_extracts_clauses_from_quicksort_benchmark(tmp_path: Path) -> None:
    repo_quicksort = Path(__file__).resolve().parents[2] / "eval/benchmarks/quicksort/quicksort.c"
    if not repo_quicksort.exists():
        return
    spec = extract_function_spec(str(repo_quicksort), "swap")
    assert spec is not None
    assert spec.return_type_text == "void"
    assert [p.name for p in spec.parameters] == ["a", "b"]
    assert len(spec.requires) == 2
    assert len(spec.ensures) == 2
    assert spec.assigns == ["*a, *b"]
