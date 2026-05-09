"""Tests for the JSONL output emitted by eval/verify_program.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from eval.verify_program import (  # noqa: E402
    FunctionVerificationResult,
    ProgramVerificationResult,
    _write_jsonl,
)


def test_write_jsonl_emits_one_record_per_function(tmp_path: Path) -> None:
    output = tmp_path / "results.jsonl"
    results = {
        "a.c": ProgramVerificationResult(
            file="a.c",
            skipped_function_names=["unannotated_helper"],
            vresults=[
                FunctionVerificationResult("a.c", "passing_fn", 0, []),
                FunctionVerificationResult("a.c", "failing_fn", 1, ["assertion FAILURE"]),
            ],
        ),
    }

    _write_jsonl(output, results)
    records = [json.loads(line) for line in output.read_text().splitlines()]

    assert len(records) == 3
    verified = [r for r in records if r["kind"] == "verified"]
    skipped = [r for r in records if r["kind"] == "skipped"]
    assert len(verified) == 2
    assert len(skipped) == 1
    assert {(r["function"], r["passed"]) for r in verified} == {
        ("passing_fn", True),
        ("failing_fn", False),
    }
    assert skipped[0] == {
        "kind": "skipped",
        "file": "a.c",
        "function": "unannotated_helper",
        "reason": "no_annotations",
    }


def test_write_jsonl_preserves_failure_details(tmp_path: Path) -> None:
    output = tmp_path / "results.jsonl"
    results = {
        "x.c": ProgramVerificationResult(
            file="x.c",
            skipped_function_names=[],
            vresults=[
                FunctionVerificationResult(
                    "x.c", "f", 10, ["line 1: assertion FAILURE", "line 2: pointer FAILURE"]
                ),
            ],
        ),
    }

    _write_jsonl(output, results)
    record = json.loads(output.read_text().splitlines()[0])

    assert record["returncode"] == 10
    assert record["failures"] == ["line 1: assertion FAILURE", "line 2: pointer FAILURE"]
    assert record["passed"] is False


def test_write_jsonl_overwrites_existing_file(tmp_path: Path) -> None:
    output = tmp_path / "results.jsonl"
    output.write_text("stale content that should be replaced\n")
    results = {
        "y.c": ProgramVerificationResult(
            file="y.c",
            skipped_function_names=[],
            vresults=[FunctionVerificationResult("y.c", "fresh", 0, [])],
        ),
    }

    _write_jsonl(output, results)
    lines = output.read_text().splitlines()

    assert len(lines) == 1
    assert json.loads(lines[0])["function"] == "fresh"
