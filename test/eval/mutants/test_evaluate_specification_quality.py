"""Tests for the parallel driver in `eval.mutants.evaluate_specification_quality`."""

import io
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from eval.mutants import evaluate_specification_quality as driver
from tools.util.mutation import MutationScore, NoMutantsGenerated


def _fake_scorer(file_path: str, function: str, *, keep_artifacts: bool, include_dirs: list[str]):
    """Stand-in for `generate_mutants_and_compute_score` that finishes in reverse name order."""
    del keep_artifacts, include_dirs
    # `swap` sleeps longest so a driver that wrote records as they *complete* would misorder them.
    time.sleep({"partition": 0.05, "quickSort": 0.1, "swap": 0.2}[function])
    if function == "swap":
        return NoMutantsGenerated(file_path, function)
    return MutationScore(
        file=file_path,
        target_function=function,
        num_mutants=2,
        num_killed=1,
        num_survived=1,
        num_timed_out=0,
        num_compile_failed=0,
        num_instrumentation_failed=0,
        kill_score=0.5,
    )


def test_records_are_written_in_function_order_regardless_of_completion_order(
    monkeypatch,
) -> None:
    monkeypatch.setattr(driver, "generate_mutants_and_compute_score", _fake_scorer)
    out = io.StringIO()
    source = Path("test/data/quicksort.c")
    with ThreadPoolExecutor(max_workers=4) as executor:
        submitted = driver._submit_file(
            source=source,
            auto_include=False,
            include_dirs=[],
            run_mutation=True,
            run_redundancy=False,
            keep_artifacts=False,
            executor=executor,
        )
        driver._write_file_records(submitted, out=out)

    records = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [record["function"] for record in records] == ["partition", "quickSort", "swap"]
    assert [record["was_mutation_tested"] for record in records] == [True, True, False]
    assert records[0]["kill_score"] == "0.5000"
    assert records[2]["metadata"].endswith("no mutable operators")


class _FakeRedundancyScore:
    def __init__(self, function: str) -> None:
        self.function = function

    def summary(self) -> dict:
        return {"kind": "clause_redundancy_summary", "function": self.function}


def _fake_redundancy_scorer(file_path: str, function: str, *, keep_artifacts: bool):
    """Stand-in for `compute_clause_redundancy_score` that finishes in reverse name order."""
    del file_path, keep_artifacts
    time.sleep({"partition": 0.05, "quickSort": 0.1, "swap": 0.2}[function])
    # `quickSort` stands for a function whose baseline does not verify, which yields no record.
    return None if function == "quickSort" else _FakeRedundancyScore(function)


def test_redundancy_records_follow_mutation_records_in_function_order(monkeypatch) -> None:
    monkeypatch.setattr(driver, "generate_mutants_and_compute_score", _fake_scorer)
    monkeypatch.setattr(driver, "compute_clause_redundancy_score", _fake_redundancy_scorer)
    out = io.StringIO()
    with ThreadPoolExecutor(max_workers=6) as executor:
        submitted = driver._submit_file(
            source=Path("test/data/quicksort.c"),
            auto_include=False,
            include_dirs=[],
            run_mutation=True,
            run_redundancy=True,
            keep_artifacts=False,
            executor=executor,
        )
        driver._write_file_records(submitted, out=out)

    records = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [(record["kind"], record["function"]) for record in records] == [
        ("mutation_summary", "partition"),
        ("mutation_summary", "quickSort"),
        ("mutation_summary", "swap"),
        ("clause_redundancy_summary", "partition"),
        ("clause_redundancy_summary", "swap"),
    ]


def test_unannotated_file_submits_nothing(monkeypatch) -> None:
    monkeypatch.setattr(driver, "generate_mutants_and_compute_score", _fake_scorer)
    monkeypatch.setattr(driver, "compute_clause_redundancy_score", _fake_redundancy_scorer)
    with ThreadPoolExecutor(max_workers=1) as executor:
        submitted = driver._submit_file(
            source=Path("test/data/no_callees.c"),
            auto_include=False,
            include_dirs=[],
            run_mutation=True,
            run_redundancy=True,
            keep_artifacts=False,
            executor=executor,
        )
    assert submitted.functions == []
    assert submitted.mutation_futures == []
    assert submitted.redundancy_futures == []
