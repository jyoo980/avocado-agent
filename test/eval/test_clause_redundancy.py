"""Tests for eval/clause_redundancy.py — orchestration around clause-removal mutation."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from eval import clause_redundancy  # noqa: E402
from eval.clause_redundancy import score_clause_redundancy  # noqa: E402


def _write_c(tmp_path: Path, source: str) -> str:
    path = tmp_path / "sample.c"
    path.write_text(source)
    return str(path)


def test_all_clauses_redundant_when_cbmc_always_passes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    file_path = _write_c(
        tmp_path,
        """
        int g(int a, int b)
        __CPROVER_requires(a >= 0)
        __CPROVER_requires(b >= 0)
        __CPROVER_ensures(__CPROVER_return_value == a + b)
        { return a + b; }
        """,
    )
    monkeypatch.setattr(clause_redundancy, "run_cbmc", lambda **_: ("ok", 0))
    score = score_clause_redundancy(file_path, "g")
    assert score.total_clauses == 3
    assert score.redundant == 3
    assert score.required == 0
    assert score.redundancy_rate == 1.0


def test_no_clauses_redundant_when_cbmc_always_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    file_path = _write_c(
        tmp_path,
        """
        int g(int a, int b)
        __CPROVER_requires(a >= 0)
        __CPROVER_ensures(__CPROVER_return_value == a + b)
        { return a + b; }
        """,
    )
    monkeypatch.setattr(clause_redundancy, "run_cbmc", lambda **_: ("fail", 1))
    score = score_clause_redundancy(file_path, "g")
    assert score.redundant == 0
    assert score.required == score.total_clauses
    assert score.redundancy_rate == 0.0


def test_mutant_files_are_cleaned_up(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    file_path = _write_c(
        tmp_path,
        """
        int g(int a)
        __CPROVER_requires(a > 0)
        __CPROVER_ensures(__CPROVER_return_value == a)
        { return a; }
        """,
    )
    monkeypatch.setattr(clause_redundancy, "run_cbmc", lambda **_: ("ok", 0))
    score_clause_redundancy(file_path, "g")
    leftover = list(tmp_path.glob("sample__clause_drop_*.c"))
    assert leftover == []


def test_run_cbmc_called_once_per_clause(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    file_path = _write_c(
        tmp_path,
        """
        int g(int a, int b)
        __CPROVER_requires(a >= 0)
        __CPROVER_requires(b >= 0)
        __CPROVER_ensures(__CPROVER_return_value == a + b)
        { return a + b; }
        """,
    )
    calls: list[dict] = []

    def fake_run(**kwargs: object) -> tuple[str, int]:
        calls.append(kwargs)
        return ("ok", 0)

    monkeypatch.setattr(clause_redundancy, "run_cbmc", fake_run)
    score = score_clause_redundancy(file_path, "g")
    assert len(calls) == score.total_clauses
    for call in calls:
        assert call["function_to_verify"] == "g"
