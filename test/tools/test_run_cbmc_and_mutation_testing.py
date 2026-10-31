"""Tests for the `avocado-run-cbmc` entry point in `tools.run_cbmc_and_mutation_testing`."""

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

import tools.util.mutation
import tools.util.vacuity
from tools.run_cbmc import CbmcStep, RunCbmcResult, run_cbmc
from tools.run_cbmc_and_mutation_testing import (
    _log_mutation_feedback,
    _log_verification_attempt,
    main,
    verify_function,
)
from tools.util.edit_rules import hash_for_function_definition
from tools.util.mutation import MutationScore, NoMutantsGenerated, generate_mutants_and_compute_score


def test_run_cbmc_leaves_no_goto_files_in_the_working_directory(tmp_path: Path) -> None:
    src = tmp_path / "quicksort.c"
    shutil.copy(Path("test/data/quicksort.c"), src)
    work_dir = tmp_path / "cwd"
    work_dir.mkdir()
    original_cwd = Path.cwd()
    try:
        os.chdir(work_dir)
        # A relative source path must keep working even though the pipeline runs elsewhere.
        result = run_cbmc("swap", os.path.relpath(src, work_dir))
    finally:
        os.chdir(original_cwd)
    assert result.is_function_verified, result.response
    assert list(work_dir.iterdir()) == [], "pipeline intermediates leaked into the cwd"
    assert not list(tmp_path.glob("*.goto")), "pipeline intermediates leaked next to the source"


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
    assert record["definition_sha256"] == hash_for_function_definition(src.read_bytes(), "swap")
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
        file_path=str(src), target_function="partition", skip_reverification=True, depth=300
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
