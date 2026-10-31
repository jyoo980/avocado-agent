"""Tests for the `avocado-run-cbmc` entry point in `tools.run_cbmc_and_mutation_testing`."""

import json
from pathlib import Path

from tools.run_cbmc import _DEFAULT_RUN_CBMC_TIMEOUT_SEC
from tools.run_cbmc_and_mutation_testing import _AGENT_MUTANT_TIMEOUT_SEC, _log_mutation_feedback
from tools.util.mutation import DEFAULT_MUTANT_TIMEOUT_SEC, MutationScore, NoMutantsGenerated


def test_mutation_feedback_of_each_passing_run_is_logged_beside_the_file(tmp_path: Path) -> None:
    src = tmp_path / "quicksort.c"
    src.write_text("int f(void) { return 0; }\n")
    scores = [(1, 3, 0.25), (2, 2, 0.5)]
    for killed, survived, kill_score in scores:
        score = MutationScore(
            file=str(src),
            target_function="partition",
            num_mutants=6,
            num_killed=killed,
            num_survived=survived,
            num_timed_out=1,
            num_compile_failed=1,
            num_instrumentation_failed=0,
            kill_score=kill_score,
        )
        _log_mutation_feedback(str(src), "partition", score, seconds=12.34)
    _log_mutation_feedback(str(src), "swap", NoMutantsGenerated(str(src), "swap"), seconds=0.01)
    log = tmp_path / "quicksort-mutation-feedback.jsonl"
    records = [json.loads(line) for line in log.read_text().splitlines()]
    # One record per passing run, in order, so successive scores can be compared.
    assert [record["kill_score"] for record in records] == [0.25, 0.5, None]
    first = records[0]
    assert first["function"] == "partition" and first["was_mutation_tested"] is True
    assert (first["total"], first["killed"], first["survived"]) == (6, 1, 3)
    assert (first["timed_out"], first["compile_failed"]) == (1, 1)
    assert first["instrumentation_failed"] == 0 and first["seconds"] == 12.3
    assert records[2]["was_mutation_tested"] is False
    assert "no mutable operators" in records[2]["reason"]
    # The attempts log, which `avocado-verify` counts records of, is a different file.
    assert not (tmp_path / "quicksort-verification-attempts.jsonl").exists()


def test_agent_tool_uses_a_shorter_mutant_budget_than_the_metric() -> None:
    # The evaluation metric keeps the full pipeline timeout; only the agent-facing tool trims it.
    assert DEFAULT_MUTANT_TIMEOUT_SEC == _DEFAULT_RUN_CBMC_TIMEOUT_SEC
    assert 0 < _AGENT_MUTANT_TIMEOUT_SEC < DEFAULT_MUTANT_TIMEOUT_SEC
