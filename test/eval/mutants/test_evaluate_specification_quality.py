"""Tests for the parallel driver in `eval.mutants.evaluate_specification_quality`."""

import io
import json
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

import pytest

from eval.mutants import evaluate_specification_quality as driver
from eval.mutants.mutate_function import Mutant
from tools.util.mutation import (
    MutantVerificationResult,
    NoMutantsGenerated,
    _aggregate_mutation_score,
)


def _fake_scorer(file_path: str, function: str, *, keep_artifacts: bool, include_dirs: list[str]):
    """Stand-in for `generate_mutants_and_compute_score` that finishes in reverse name order."""
    del keep_artifacts, include_dirs
    # `swap` sleeps longest so a driver that wrote records as they *complete* would misorder them.
    time.sleep({"partition": 0.05, "quickSort": 0.1, "swap": 0.2}[function])
    if function == "swap":
        return NoMutantsGenerated(file_path, function)
    # One killed and one surviving mutant, so the kill score is 0.5.
    mutant = Mutant(
        function=function,
        operator_class="RELATIONAL",
        original_operator="<",
        replacement_operator="<=",
        start_byte=0,
        end_byte=2,
        line=1,
        column=0,
        mutant_source="",
    )
    vresults = [
        MutantVerificationResult(
            mutant=mutant, path_to_mutant="m.c", killed=killed, returncode=10 if killed else 0
        )
        for killed in (True, False)
    ]
    return _aggregate_mutation_score(vresults, file_path, function)


class _FakeRedundancyScore:
    """Stand-in for a clause-redundancy score whose summary names its function."""

    def __init__(self, function: str) -> None:
        self.function = function

    def summary(self) -> dict:
        return {"kind": "redundancy_summary", "function": self.function}


def _fake_redundancy(file_path: str, function: str, *, keep_artifacts: bool):
    """Stand-in for `compute_clause_redundancy_score`."""
    del file_path, keep_artifacts
    return _FakeRedundancyScore(function)


def _score_and_write(*, run_mutation: bool, run_redundancy: bool) -> list[dict]:
    """Run the driver on `test/data/quicksort.c` and return the records it writes."""
    out = io.StringIO()
    failure: Future[None] = Future()
    with ThreadPoolExecutor(max_workers=4) as executor:
        submitted = driver._submit_file(
            source=Path("test/data/quicksort.c"),
            auto_include=False,
            include_dirs=[],
            run_mutation=run_mutation,
            keep_artifacts=False,
            executor=executor,
            failure=failure,
        )
        driver._write_file_records(
            submitted,
            out=out,
            run_redundancy=run_redundancy,
            keep_artifacts=False,
            failure=failure,
        )
    return [json.loads(line) for line in out.getvalue().splitlines()]


def test_records_are_written_in_function_order_regardless_of_completion_order(
    monkeypatch,
) -> None:
    monkeypatch.setattr(driver, "generate_mutants_and_compute_score", _fake_scorer)
    records = _score_and_write(run_mutation=True, run_redundancy=False)
    assert [record["function"] for record in records] == ["partition", "quickSort", "swap"]
    assert [record["was_mutation_tested"] for record in records] == [True, True, False]
    assert float(records[0]["kill_score"]) == pytest.approx(0.5)
    assert records[2]["metadata"].endswith("no mutable operators")


def test_each_functions_mutation_and_redundancy_records_are_adjacent(monkeypatch) -> None:
    monkeypatch.setattr(driver, "generate_mutants_and_compute_score", _fake_scorer)
    monkeypatch.setattr(driver, "compute_clause_redundancy_score", _fake_redundancy)
    records = _score_and_write(run_mutation=True, run_redundancy=True)
    assert [(record["kind"], record["function"]) for record in records] == [
        ("mutation_summary", "partition"),
        ("redundancy_summary", "partition"),
        ("mutation_summary", "quickSort"),
        ("redundancy_summary", "quickSort"),
        ("mutation_summary", "swap"),
        ("redundancy_summary", "swap"),
    ]


def test_redundancy_alone_writes_one_record_per_function(monkeypatch) -> None:
    monkeypatch.setattr(driver, "compute_clause_redundancy_score", _fake_redundancy)
    records = _score_and_write(run_mutation=False, run_redundancy=True)
    assert [record["function"] for record in records] == ["partition", "quickSort", "swap"]


def test_a_later_functions_failure_is_raised_without_waiting_for_earlier_ones(
    monkeypatch,
) -> None:
    release_partition = threading.Event()

    def scorer(file_path: str, function: str, *, keep_artifacts: bool, include_dirs: list[str]):
        if function == "partition":
            release_partition.wait(timeout=10)
        if function == "quickSort":
            message = "boom"
            raise RuntimeError(message)
        return _fake_scorer(
            file_path, function, keep_artifacts=keep_artifacts, include_dirs=include_dirs
        )

    monkeypatch.setattr(driver, "generate_mutants_and_compute_score", scorer)
    failure: Future[None] = Future()
    with ThreadPoolExecutor(max_workers=4) as executor:
        try:
            submitted = driver._submit_file(
                source=Path("test/data/quicksort.c"),
                auto_include=False,
                include_dirs=[],
                run_mutation=True,
                keep_artifacts=False,
                executor=executor,
                failure=failure,
            )
            with pytest.raises(RuntimeError, match="boom"):
                driver._write_file_records(
                    submitted,
                    out=io.StringIO(),
                    run_redundancy=False,
                    keep_artifacts=False,
                    failure=failure,
                )
            # `partition` is still being scored: the failure was not reported in function order.
            assert not submitted.mutation_futures[0].done()
        finally:
            release_partition.set()


def test_unannotated_file_submits_nothing(monkeypatch) -> None:
    monkeypatch.setattr(driver, "generate_mutants_and_compute_score", _fake_scorer)
    with ThreadPoolExecutor(max_workers=1) as executor:
        submitted = driver._submit_file(
            source=Path("test/data/no_callees.c"),
            auto_include=False,
            include_dirs=[],
            run_mutation=True,
            keep_artifacts=False,
            executor=executor,
            failure=Future(),
        )
    assert submitted.functions == []
    assert submitted.mutation_futures == []
