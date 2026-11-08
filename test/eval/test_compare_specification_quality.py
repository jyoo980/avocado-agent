"""Tests for eval/compare_specification_quality.py."""

import json
from pathlib import Path

from eval.compare_specification_quality import _get_specification_quality_comparison


def _tested(function: str, killed: int, survived: int, timed_out: int = 0) -> dict:
    """Return a mutation summary record.

    Its `kill_score` is a number, or null if no mutant was decided.
    """
    decided = killed + survived
    return {
        "kind": "mutation_summary",
        "was_mutation_tested": True,
        "file": "/benchmarks/q.c",
        "function": function,
        "total": decided + timed_out,
        "killed": killed,
        "survived": survived,
        "timed_out": timed_out,
        "compile_failed": 0,
        "instrumentation_failed": 0,
        "kill_score": round(killed / decided, 4) if decided else None,
    }


def _tested_with_string_kill_score(function: str, killed: int, survived: int) -> dict:
    """Return a mutation summary record without `was_mutation_tested`, and with a string `kill_score`.

    The `kill_score` is a string such as "0.2500".
    """
    record = _tested(function, killed, survived)
    del record["was_mutation_tested"]
    record["kill_score"] = f"{record['kill_score'] or 0.0:.4f}"
    return record


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
    assert summary["only_in_baseline"] == []
    assert summary["only_in_experimental"] == []
    assert summary["unscored_in_baseline"] == ["/benchmarks/q.c#h"]
    assert summary["unscored_in_experimental"] == ["/benchmarks/q.c#g"]
    assert summary["mean_kill_score_delta"] == 0.5


def test_unscored_functions_are_distinguished_from_absent_ones(tmp_path: Path) -> None:
    baseline = _write_jsonl(
        tmp_path / "baseline.jsonl", [_tested("f", 1, 1), _tested("g", 1, 1), _tested("h", 1, 1)]
    )
    experimental = _write_jsonl(
        tmp_path / "experimental.jsonl", [_tested("f", 2, 0), _untested("g"), _tested("i", 1, 1)]
    )

    summary = _get_specification_quality_comparison(baseline, experimental)["mutation_summary"]

    assert summary["only_in_baseline"] == ["/benchmarks/q.c#h"]
    assert summary["only_in_experimental"] == ["/benchmarks/q.c#i"]
    assert summary["unscored_in_baseline"] == []
    assert summary["unscored_in_experimental"] == ["/benchmarks/q.c#g"]


def test_unscored_functions_are_counted(tmp_path: Path) -> None:
    baseline = _write_jsonl(
        tmp_path / "baseline.jsonl", [_tested("f", 1, 1), _untested("g"), _untested("h")]
    )
    experimental = _write_jsonl(
        tmp_path / "experimental.jsonl", [_tested("f", 1, 1), _untested("g"), _untested("i")]
    )

    summary = _get_specification_quality_comparison(baseline, experimental)["mutation_summary"]

    assert summary["baseline_count"] == 1
    assert summary["baseline_unscored_count"] == 2
    assert summary["experimental_unscored_count"] == 2
    assert summary["only_in_baseline"] == []
    assert summary["only_in_experimental"] == []


def test_functions_without_decided_mutants_are_not_compared(tmp_path: Path) -> None:
    undecided = _tested_with_string_kill_score("h", 0, 0)
    undecided["timed_out"] = 3
    baseline = _write_jsonl(
        tmp_path / "baseline.jsonl", [_tested("f", 4, 1), _tested("g", 1, 1), _tested("h", 1, 1)]
    )
    experimental = _write_jsonl(
        tmp_path / "experimental.jsonl",
        [_tested("f", 0, 0, timed_out=5), _tested("g", 2, 0), undecided],
    )

    summary = _get_specification_quality_comparison(baseline, experimental)["mutation_summary"]

    assert [row["function"] for row in summary["per_function"]] == ["g"]
    assert summary["unscored_in_experimental"] == ["/benchmarks/q.c#f", "/benchmarks/q.c#h"]


def test_records_with_string_kill_score_and_without_was_mutation_tested_are_compared(
    tmp_path: Path,
) -> None:
    baseline = _write_jsonl(
        tmp_path / "baseline.jsonl", [_tested_with_string_kill_score("f", 1, 3)]
    )
    experimental = _write_jsonl(tmp_path / "experimental.jsonl", [_tested("f", 3, 1)])

    summary = _get_specification_quality_comparison(baseline, experimental)["mutation_summary"]

    assert [row["kill_score_delta"] for row in summary["per_function"]] == [0.5]


def test_rows_with_equal_deltas_are_sorted_by_function(tmp_path: Path) -> None:
    functions = ["d", "b", "e", "a", "c"]
    baseline = _write_jsonl(tmp_path / "baseline.jsonl", [_tested(f, 1, 1) for f in functions])
    experimental = _write_jsonl(
        tmp_path / "experimental.jsonl", [_tested(f, 1, 1) for f in functions]
    )

    summary = _get_specification_quality_comparison(baseline, experimental)["mutation_summary"]

    assert [row["function"] for row in summary["per_function"]] == sorted(functions)
