"""Tests for eval/compare_specification_quality.py."""

import json
from pathlib import Path

from eval.compare_specification_quality import _get_specification_quality_comparison


def _tested(function: str, killed: int, survived: int) -> dict:
    """Return a mutation summary record, as `MutationScore.summary()` writes it."""
    return {
        "kind": "mutation_summary",
        "was_mutation_tested": True,
        "file": "/benchmarks/q.c",
        "function": function,
        "total": killed + survived,
        "killed": killed,
        "survived": survived,
        "timed_out": 0,
        "compile_failed": 0,
        "instrumentation_failed": 0,
        "kill_score": f"{killed / (killed + survived):.4f}",
    }


def _untested(function: str) -> dict:
    """Return a mutation summary record of a function that was not mutation tested."""
    return {
        "kind": "mutation_summary",
        "was_mutation_tested": False,
        "file": "/benchmarks/q.c",
        "function": function,
        "metadata": f"/benchmarks/q.c#{function} did not verify; cannot score mutants",
    }


def _write_jsonl(path: Path, records: list[dict]) -> str:
    """Write the records to a JSONL file and return its path."""
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    return str(path)


def test_kill_score_delta_of_functions_tested_in_both_files(tmp_path: Path) -> None:
    baseline = _write_jsonl(tmp_path / "baseline.jsonl", [_tested("f", 1, 3)])
    experimental = _write_jsonl(tmp_path / "experimental.jsonl", [_tested("f", 3, 1)])

    summary = _get_specification_quality_comparison(baseline, experimental)["mutation_summary"]

    assert summary["mean_kill_score_delta"] == 0.5
    assert [row["kill_score_delta"] for row in summary["per_function"]] == [0.5]


def test_untested_functions_are_not_compared(tmp_path: Path) -> None:
    baseline = _write_jsonl(
        tmp_path / "baseline.jsonl",
        [_tested("f", 1, 1), _tested("g", 1, 1), _untested("h"), _untested("i")],
    )
    experimental = _write_jsonl(
        tmp_path / "experimental.jsonl",
        [_tested("f", 2, 0), _untested("g"), _tested("h", 1, 1), _untested("i")],
    )

    summary = _get_specification_quality_comparison(baseline, experimental)["mutation_summary"]

    assert summary["common_count"] == 1
    assert summary["only_in_baseline"] == ["/benchmarks/q.c#g"]
    assert summary["only_in_experimental"] == ["/benchmarks/q.c#h"]
    assert summary["mean_kill_score_delta"] == 0.5
