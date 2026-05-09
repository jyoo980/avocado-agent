"""Tests for eval/caller_replaceability.py."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from eval import caller_replaceability  # noqa: E402
from eval.caller_replaceability import (  # noqa: E402
    get_callers_of,
    score_caller_replaceability,
)
from tools.util.callgraph import CallGraph  # noqa: E402


def _write_c(tmp_path: Path, source: str) -> str:
    path = tmp_path / "fixture.c"
    path.write_text(source)
    return str(path)


def test_get_callers_of_returns_only_in_file_callers() -> None:
    cg = CallGraph(
        {
            "f": {"internal": [], "external": []},
            "g": {"internal": ["f"], "external": []},
            "h": {"internal": ["f", "g"], "external": []},
            "unrelated": {"internal": [], "external": ["printf"]},
        }
    )
    assert get_callers_of("f", cg) == ["g", "h"]
    assert get_callers_of("g", cg) == ["h"]
    assert get_callers_of("missing", cg) == []


def test_unannotated_callers_are_separated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    file_path = _write_c(
        tmp_path,
        """
        int target(int x)
        __CPROVER_requires(x > 0)
        __CPROVER_ensures(__CPROVER_return_value == x)
        { return x; }

        int annotated_caller(int x)
        __CPROVER_requires(x > 0)
        __CPROVER_ensures(__CPROVER_return_value > 0)
        { return target(x); }

        int unannotated_caller(int x)
        { return target(x); }
        """,
    )
    monkeypatch.setattr(caller_replaceability, "run_cbmc", lambda **_: ("ok", 0))
    score = score_caller_replaceability(file_path, "target")
    assert score.annotated_callers == ["annotated_caller"]
    assert score.unannotated_callers == ["unannotated_caller"]


def test_pass_rate_when_all_callers_verify(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    file_path = _write_c(
        tmp_path,
        """
        int target(int x)
        __CPROVER_requires(x > 0)
        __CPROVER_ensures(__CPROVER_return_value == x)
        { return x; }

        int caller_a(int x)
        __CPROVER_requires(x > 0)
        __CPROVER_ensures(__CPROVER_return_value > 0)
        { return target(x); }

        int caller_b(int x)
        __CPROVER_requires(x > 0)
        __CPROVER_ensures(__CPROVER_return_value > 0)
        { return target(x) + 1; }
        """,
    )
    monkeypatch.setattr(caller_replaceability, "run_cbmc", lambda **_: ("ok", 0))
    score = score_caller_replaceability(file_path, "target")
    assert score.passed == 2
    assert score.failed == 0
    assert score.pass_rate == 1.0


def test_pass_rate_when_no_callers_verify(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    file_path = _write_c(
        tmp_path,
        """
        int target(int x)
        __CPROVER_requires(x > 0)
        __CPROVER_ensures(__CPROVER_return_value == x)
        { return x; }

        int caller(int x)
        __CPROVER_requires(x > 0)
        __CPROVER_ensures(__CPROVER_return_value > 0)
        { return target(x); }
        """,
    )
    monkeypatch.setattr(caller_replaceability, "run_cbmc", lambda **_: ("fail", 1))
    score = score_caller_replaceability(file_path, "target")
    assert score.passed == 0
    assert score.failed == 1
    assert score.pass_rate == 0.0


def test_function_with_no_callers_yields_empty_score(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    file_path = _write_c(
        tmp_path,
        """
        int loner(int x)
        __CPROVER_requires(x > 0)
        __CPROVER_ensures(__CPROVER_return_value == x)
        { return x; }
        """,
    )
    monkeypatch.setattr(caller_replaceability, "run_cbmc", lambda **_: ("ok", 0))
    score = score_caller_replaceability(file_path, "loner")
    assert score.annotated_callers == []
    assert score.unannotated_callers == []
    assert score.passed == 0
    assert score.failed == 0


def test_run_cbmc_invoked_per_annotated_caller(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    file_path = _write_c(
        tmp_path,
        """
        int target(int x)
        __CPROVER_requires(x > 0)
        __CPROVER_ensures(__CPROVER_return_value == x)
        { return x; }

        int caller_a(int x)
        __CPROVER_requires(x > 0)
        __CPROVER_ensures(__CPROVER_return_value > 0)
        { return target(x); }

        int caller_b(int x)
        __CPROVER_requires(x > 0)
        __CPROVER_ensures(__CPROVER_return_value > 0)
        { return target(x); }
        """,
    )
    invocations: list[str] = []

    def fake_run(**kwargs: object) -> tuple[str, int]:
        invocations.append(str(kwargs["function_to_verify"]))
        return ("ok", 0)

    monkeypatch.setattr(caller_replaceability, "run_cbmc", fake_run)
    score_caller_replaceability(file_path, "target")
    assert sorted(invocations) == ["caller_a", "caller_b"]
