"""Tests for eval/mutation_score.py — orchestration around the mutation engine.

`run_cbmc` is monkeypatched so these tests don't depend on a CBMC install. Real CBMC runs
are exercised by the docker-based smoke flow in `make run`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from eval import mutation_score  # noqa: E402
from eval.mutation_score import score_function_mutations  # noqa: E402


def _write_c(tmp_path: Path, source: str) -> str:
    path = tmp_path / "sample.c"
    path.write_text(source)
    return str(path)


def test_kill_rate_when_all_mutants_fail(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
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
    monkeypatch.setattr(mutation_score, "run_cbmc", lambda **_: ("failed", 1))
    score = score_function_mutations(file_path, "min")
    assert score.total > 0
    assert score.killed == score.total
    assert score.survived == 0
    assert score.kill_rate == 1.0


def test_kill_rate_when_no_mutants_fail(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    file_path = _write_c(
        tmp_path,
        """
        int weakly_specced(int a, int b)
        __CPROVER_requires(1)
        __CPROVER_ensures(1)
        {
            if (a < b) return a;
            return b;
        }
        """,
    )
    monkeypatch.setattr(mutation_score, "run_cbmc", lambda **_: ("ok", 0))
    score = score_function_mutations(file_path, "weakly_specced")
    assert score.total > 0
    assert score.killed == 0
    assert score.survived == score.total
    assert score.kill_rate == 0.0


def test_mutant_files_are_cleaned_up(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    file_path = _write_c(
        tmp_path,
        """
        int g(int a, int b)
        __CPROVER_ensures(__CPROVER_return_value == a + b)
        { return a + b; }
        """,
    )
    monkeypatch.setattr(mutation_score, "run_cbmc", lambda **_: ("ok", 0))
    score_function_mutations(file_path, "g")
    leftover = list(tmp_path.glob("sample__mutant_*.c"))
    assert leftover == []


def test_keep_artifacts_preserves_mutant_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    file_path = _write_c(
        tmp_path,
        """
        int g(int a, int b)
        __CPROVER_ensures(__CPROVER_return_value == a + b)
        { return a + b; }
        """,
    )
    monkeypatch.setattr(mutation_score, "run_cbmc", lambda **_: ("ok", 0))
    score = score_function_mutations(file_path, "g", keep_artifacts=True)
    leftover = sorted(tmp_path.glob("sample__mutant_*.c"))
    assert len(leftover) == score.total


def test_run_cbmc_called_once_per_mutant(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    file_path = _write_c(
        tmp_path,
        """
        int sub(int a, int b)
        __CPROVER_ensures(__CPROVER_return_value == a - b)
        { return a - b; }
        """,
    )
    calls: list[dict] = []

    def fake_run(**kwargs: object) -> tuple[str, int]:
        calls.append(kwargs)
        return ("ok", 0)

    monkeypatch.setattr(mutation_score, "run_cbmc", fake_run)
    score = score_function_mutations(file_path, "sub")
    assert len(calls) == score.total
    # The function name must be passed verbatim and the mutant file must be a temp file
    # in the source directory (not the original).
    for call in calls:
        assert call["function_to_verify"] == "sub"
        path = Path(str(call["file_containing_function_to_verify"]))
        assert path.parent == tmp_path
        assert path.name.startswith("sample__mutant_")


def test_zero_mutants_yields_zero_kill_rate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    file_path = _write_c(
        tmp_path,
        """
        int passthrough(int a)
        __CPROVER_ensures(__CPROVER_return_value == a)
        { return a; }
        """,
    )
    monkeypatch.setattr(mutation_score, "run_cbmc", lambda **_: ("ok", 0))
    score = score_function_mutations(file_path, "passthrough")
    assert score.total == 0
    assert score.kill_rate == 0.0
