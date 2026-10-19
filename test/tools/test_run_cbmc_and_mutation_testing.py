"""Tests for the `avocado-run-cbmc` entry point in `tools.run_cbmc_and_mutation_testing`."""

import json
import os
import shutil
from pathlib import Path

from tools.run_cbmc_and_mutation_testing import _log_mutation_feedback, verify_function
from tools.util.mutation import MutationScore, NoMutantsGenerated


def test_verify_function_leaves_no_goto_files_in_the_working_directory(tmp_path: Path) -> None:
    src = tmp_path / "quicksort.c"
    shutil.copy(Path("test/data/quicksort.c"), src)
    work_dir = tmp_path / "cwd"
    work_dir.mkdir()
    original_cwd = Path.cwd()
    try:
        os.chdir(work_dir)
        # A relative source path must keep working even though the pipeline runs elsewhere.
        result = verify_function("swap", os.path.relpath(src, work_dir))
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
