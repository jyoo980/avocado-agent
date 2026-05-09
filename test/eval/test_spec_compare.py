"""Tests for eval/spec_compare.py."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from eval import spec_compare  # noqa: E402
from eval.spec_compare import (  # noqa: E402
    build_precondition_harness,
    compare_preconditions,
    compare_via_verification,
)
from tools.spec_extract import extract_function_spec  # noqa: E402


def _write_c(tmp_path: Path, name: str, source: str) -> str:
    path = tmp_path / name
    path.write_text(source)
    return str(path)


def _versions(tmp_path: Path) -> tuple[str, str]:
    a = _write_c(
        tmp_path,
        "ver_a.c",
        """
        int abs_val(int x)
        __CPROVER_requires(x > -1000)
        __CPROVER_ensures(__CPROVER_return_value >= 0)
        { return x < 0 ? -x : x; }
        """,
    )
    b = _write_c(
        tmp_path,
        "ver_b.c",
        """
        int abs_val(int x)
        __CPROVER_requires(x > -1000 && x < 1000)
        __CPROVER_ensures(__CPROVER_return_value >= 0)
        { return x < 0 ? -x : x; }
        """,
    )
    return a, b


def test_verification_classifies_both_pass(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    a, b = _versions(tmp_path)
    monkeypatch.setattr(spec_compare, "run_cbmc", lambda **_: ("ok", 0))
    result = compare_via_verification(a, b, "abs_val")
    assert result.classification == "both_verify"
    assert result.a_passes and result.b_passes


def test_verification_classifies_only_a_passes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    a, b = _versions(tmp_path)
    calls: list[str] = []

    def fake_run(**kwargs: object) -> tuple[str, int]:
        path = str(kwargs["file_containing_function_to_verify"])
        calls.append(path)
        return ("", 0 if path == a else 1)

    monkeypatch.setattr(spec_compare, "run_cbmc", fake_run)
    result = compare_via_verification(a, b, "abs_val")
    assert result.classification == "only_a_verifies"
    assert calls == [a, b]


def test_verification_classifies_neither(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    a, b = _versions(tmp_path)
    monkeypatch.setattr(spec_compare, "run_cbmc", lambda **_: ("fail", 1))
    result = compare_via_verification(a, b, "abs_val")
    assert result.classification == "neither"


def test_build_precondition_harness_includes_both_directions(tmp_path: Path) -> None:
    a, b = _versions(tmp_path)
    spec_a = extract_function_spec(a, "abs_val")
    spec_b = extract_function_spec(b, "abs_val")
    assert spec_a is not None and spec_b is not None
    harness = build_precondition_harness(a, spec_a, spec_b)
    # Original source is appended to, so the body is preserved.
    assert "return x < 0 ? -x : x;" in harness
    # Both check functions are present.
    assert "_check_a_implies_b" in harness
    assert "_check_b_implies_a" in harness
    # The conjunctions were assembled.
    assert "x > -1000" in harness
    assert "x < 1000" in harness
    # Asserts go in the right direction (a→b assumes a, asserts b).
    assert harness.index("_check_a_implies_b") < harness.index("_check_b_implies_a")


def test_compare_preconditions_classifies_a_stronger(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When a's pre is stricter (subset of b's accepted inputs), only a→b verifies."""
    # Here `a` requires `x > 0`; `b` requires `x > -1`. Every x satisfying a's pre also
    # satisfies b's pre (a→b), but not vice versa.
    a = _write_c(
        tmp_path,
        "stricter.c",
        """
        int g(int x)
        __CPROVER_requires(x > 0)
        __CPROVER_ensures(__CPROVER_return_value == x)
        { return x; }
        """,
    )
    b = _write_c(
        tmp_path,
        "looser.c",
        """
        int g(int x)
        __CPROVER_requires(x > -1)
        __CPROVER_ensures(__CPROVER_return_value == x)
        { return x; }
        """,
    )

    def fake_run(**kwargs: object) -> tuple[str, int]:
        if kwargs["function_to_verify"] == "_check_a_implies_b":
            return ("ok", 0)
        return ("fail", 1)

    monkeypatch.setattr(spec_compare, "run_cbmc", fake_run)
    result = compare_preconditions(a, b, "g")
    assert result is not None
    assert result.a_implies_b is True
    assert result.b_implies_a is False
    assert result.classification == "a_stronger"


def test_compare_preconditions_returns_none_when_requires_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    a = _write_c(
        tmp_path,
        "no_pre.c",
        """
        int g(int x)
        __CPROVER_ensures(__CPROVER_return_value == x)
        { return x; }
        """,
    )
    b = _write_c(
        tmp_path,
        "with_pre.c",
        """
        int g(int x)
        __CPROVER_requires(x > 0)
        __CPROVER_ensures(__CPROVER_return_value == x)
        { return x; }
        """,
    )
    monkeypatch.setattr(spec_compare, "run_cbmc", lambda **_: ("ok", 0))
    assert compare_preconditions(a, b, "g") is None


def test_compare_preconditions_cleans_up_harness(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    a, b = _versions(tmp_path)
    monkeypatch.setattr(spec_compare, "run_cbmc", lambda **_: ("ok", 0))
    compare_preconditions(a, b, "abs_val")
    assert list(tmp_path.glob("*pre_implication_harness.c")) == []
