"""Tests for the `avocado-run-cbmc` entry point in `tools.run_cbmc_and_mutation_testing`."""

import json
import shutil
import sys
from pathlib import Path

import pytest

import tools.util.mutation
import tools.util.vacuity
from tools.run_cbmc import _DEFAULT_RUN_CBMC_TIMEOUT_SEC, CbmcStep, RunCbmcResult
from tools.run_cbmc_and_mutation_testing import (
    _AGENT_MUTANT_TIMEOUT_SEC,
    _log_verification_attempt,
    main,
    verify_function,
)
from tools.util.mutation import (
    DEFAULT_MUTANT_TIMEOUT_SEC,
    MutationScore,
    generate_mutants_and_compute_score,
)


def test_the_depth_of_a_run_reaches_the_record_the_probe_and_the_mutants(
    tmp_path: Path, monkeypatch
) -> None:
    src = tmp_path / "quicksort.c"
    shutil.copy(Path("test/data/quicksort.c"), src)
    probe_depths: list[int] = []
    real_probe_run = tools.util.vacuity.run_cbmc

    def spy(*args, **kwargs):
        probe_depths.append(kwargs["depth"])
        return real_probe_run(*args, **kwargs)

    monkeypatch.setattr(tools.util.vacuity, "run_cbmc", spy)
    result = verify_function("swap", str(src), depth=300)
    assert result.is_function_verified and result.depth == 300, result.response
    # The vacuity probe was made at the same depth as the run it checked.
    assert probe_depths == [300]
    _log_verification_attempt(str(src), "swap", result)
    records = (tmp_path / "quicksort-verification-attempts.jsonl").read_text().splitlines()
    record = json.loads(records[-1])
    assert record["depth"] == 300
    runs = [json.loads(line) for line in (tmp_path / "quicksort-cbmc-runs.jsonl").read_text().splitlines()]
    assert [run["depth"] for run in runs] == [300]
    cbmc_steps = [step for step in runs[0]["subprocess_results"] if step["step"] == "cbmc"]
    assert cbmc_steps and all(step["command"].endswith("--depth 300") for step in cbmc_steps)
    # Mutants are judged at the same depth: every mutant's run is asked for it. (`swap` has no
    # operator to mutate, so `partition` is scored, with the pipeline stubbed out: the point is
    # what each run is asked for, not its verdict.)
    mutant_depths: list[int] = []

    def mutant_spy(function_to_verify, *args, **kwargs):
        del args
        mutant_depths.append(kwargs["depth"])
        return RunCbmcResult(function_to_verify, CbmcStep.CBMC, False, 10, "", depth=kwargs["depth"])

    monkeypatch.setattr(tools.util.mutation, "run_cbmc", mutant_spy)
    score = generate_mutants_and_compute_score(
        file_path=str(src),
        target_function="partition",
        is_function_already_verified=True,
        depth=300,
    )
    assert isinstance(score, MutationScore) and score.num_mutants == len(mutant_depths) > 0
    assert set(mutant_depths) == {300}


def test_a_depth_below_the_floor_is_rejected_before_anything_runs(tmp_path: Path, monkeypatch) -> None:
    src = tmp_path / "quicksort.c"
    shutil.copy(Path("test/data/quicksort.c"), src)
    monkeypatch.setattr(
        sys, "argv", ["avocado-run-cbmc", "--function", "swap", "--file", str(src), "--depth", "100"]
    )
    with pytest.raises(SystemExit) as stop:
        main()
    assert stop.value.code == 2
    assert not (tmp_path / "quicksort-verification-attempts.jsonl").exists()
    assert not (tmp_path / "quicksort-cbmc-runs.jsonl").exists()


def test_agent_tool_uses_a_shorter_mutant_budget_than_the_metric() -> None:
    # The evaluation metric keeps the full pipeline timeout; only the agent-facing tool trims it.
    assert DEFAULT_MUTANT_TIMEOUT_SEC == _DEFAULT_RUN_CBMC_TIMEOUT_SEC
    assert 0 < _AGENT_MUTANT_TIMEOUT_SEC < DEFAULT_MUTANT_TIMEOUT_SEC
