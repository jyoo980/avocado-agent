"""Tests for the `avocado-run-cbmc` entry point in `tools.run_cbmc_and_mutation_testing`."""

import json
from collections import Counter
from pathlib import Path

from tools.run_cbmc import _DEFAULT_RUN_CBMC_TIMEOUT_SEC
from tools.run_cbmc_and_mutation_testing import _AGENT_MUTANT_TIMEOUT_SEC, _log_mutation_feedback
from tools.util.mutation import (
    DEFAULT_MUTANT_TIMEOUT_SEC,
    MutantOutcome,
    MutationScore,
    NoMutantsGenerated,
)


def test_mutation_feedback_of_each_passing_run_is_logged_beside_the_file(tmp_path: Path) -> None:
    src = tmp_path / "quicksort.c"
    src.write_text("int f(void) { return 0; }\n")
    scores = [(1, 3, 0.25), (2, 2, 0.5)]
    for i, (killed, survived, kill_score) in enumerate(scores):
        score = MutationScore(
            file=str(src),
            target_function="partition",
            num_mutants=6,
            outcome_counts=Counter(
                {
                    MutantOutcome.KILLED: killed,
                    MutantOutcome.SURVIVED: survived,
                    MutantOutcome.TIMED_OUT: 1,
                    MutantOutcome.COMPILE_FAILED: 1,
                }
            ),
            kill_score=kill_score,
        )
        _log_mutation_feedback(
            str(src), "partition", score, attempt_id=f"attempt{i}", seconds=12.34
        )
    _log_mutation_feedback(
        str(src),
        "swap",
        NoMutantsGenerated(str(src), "swap"),
        attempt_id="attempt2",
        seconds=0.01,
    )
    log = tmp_path / "quicksort-mutation-feedback.jsonl"
    records = [json.loads(line) for line in log.read_text().splitlines()]
    # One record per passing run, in order, so successive scores can be compared.
    assert [record["kill_score"] for record in records] == [0.25, 0.5, None]
    # Each record names its attempt, so it can be joined to the verification-attempts log.
    assert [record["attempt_id"] for record in records] == ["attempt0", "attempt1", "attempt2"]
    first = records[0]
    assert first["function"] == "partition" and first["was_mutation_tested"] is True
    assert (first["total"], first["killed"], first["survived"]) == (6, 1, 3)
    assert (first["timed_out"], first["compile_failed"]) == (1, 1)
    assert first["instrumentation_failed"] == 0 and first["seconds"] == 12.3
    assert records[2]["was_mutation_tested"] is False
    assert "no mutable operators" in records[2]["reason"]
    # The attempts log, which `avocado-verify` counts records of, is a different file.
    assert not (tmp_path / "quicksort-verification-attempts.jsonl").exists()


def test_mutation_feedback_with_no_decided_mutant_has_a_null_kill_score(tmp_path: Path) -> None:
    src = tmp_path / "quicksort.c"
    src.write_text("int f(void) { return 0; }\n")
    score = MutationScore(
        file=str(src),
        target_function="partition",
        num_mutants=2,
        outcome_counts=Counter({MutantOutcome.TIMED_OUT: 2}),
        kill_score=0.0,
    )
    _log_mutation_feedback(str(src), "partition", score, attempt_id="attempt0", seconds=1.0)
    log = tmp_path / "quicksort-mutation-feedback.jsonl"
    (record,) = [json.loads(line) for line in log.read_text().splitlines()]
    # A score that no mutant decided does not measure the spec.
    assert record["was_mutation_tested"] is True and record["kill_score"] is None
    assert record["timed_out"] == 2


def test_agent_tool_uses_a_shorter_mutant_budget_than_the_metric() -> None:
    # The evaluation metric keeps the full pipeline timeout; only the agent-facing tool trims it.
    assert DEFAULT_MUTANT_TIMEOUT_SEC == _DEFAULT_RUN_CBMC_TIMEOUT_SEC
    assert 0 < _AGENT_MUTANT_TIMEOUT_SEC < DEFAULT_MUTANT_TIMEOUT_SEC
