"""Tests for the run_cbmc tool."""

import json
import os
import shutil
from pathlib import Path
from subprocess import CompletedProcess

from eval.mutants.mutate_function import Mutant
from tools.run_cbmc import (
    CbmcStep,
    RunCbmcResult,
    _MAX_RESPONSE_CHARS,
    _format_failure_response,
    _get_cbmc_check_command,
    _get_goto_cc_command,
    _get_goto_instrument_add_library_command,
    _get_goto_instrument_contract_command,
    _get_goto_instrument_unwind_command,
    _run_command,
    compile_with_goto_cc,
    run_cbmc,
)
from tools.util.mutation import MutantVerificationResult, MutationScore, _MAX_MUTATION_SECTION_CHARS, _print_mutation_progress, get_mutation_testing_results_for_client


def _make_mutant(
    *, source: str, original_operator: str, replacement_operator: str, start_byte: int, line: int
) -> Mutant:
    """Build a Mutant whose `get_unified_diff` reconstructs the original via the byte offset."""
    return Mutant(
        function="f",
        operator_class="RELATIONAL",
        original_operator=original_operator,
        replacement_operator=replacement_operator,
        start_byte=start_byte,
        end_byte=start_byte + len(replacement_operator),
        line=line,
        column=0,
        mutant_source=source,
    )


def _vresult(mutant: Mutant, *, killed: bool) -> MutantVerificationResult:
    return MutantVerificationResult(
        mutant=mutant, path_to_mutant="m.c", killed=killed, returncode=0 if killed else 10
    )


def test_get_goto_cc_command_no_stubs_no_includes() -> None:
    command = _get_goto_cc_command(
        function="swap", file_containing_function="quicksort.c"
    )
    assert command == "goto-cc  -o swap.goto  quicksort.c  --function swap"


def test_get_goto_instrument_unwind_command_uses_partial_loops_and_unwind() -> None:
    command = _get_goto_instrument_unwind_command("swap")
    assert command == "goto-instrument --partial-loops --unwind 5 swap.goto swap.goto"


def test_get_goto_instrument_contract_command_no_callees() -> None:
    command = _get_goto_instrument_contract_command("swap", callees=[])
    assert command == (
        "goto-instrument --enforce-contract swap swap.goto checking-swap-contracts.goto"
    )


def test_get_goto_instrument_contract_command_includes_self_for_inductive_recursive_verification() -> (
    None
):
    command = _get_goto_instrument_contract_command(
        "quickSort", callees=["partition", "quickSort"]
    )
    assert "--replace-call-with-contract partition" in command
    assert "--replace-call-with-contract quickSort" in command
    assert "--enforce-contract quickSort" in command


def test_get_goto_instrument_add_library_command_targets_the_instrumented_binary() -> None:
    command = _get_goto_instrument_add_library_command("swap")
    assert command == (
        "goto-instrument --add-library checking-swap-contracts.goto checking-swap-contracts.goto"
    )


def test_get_goto_instrument_unwind_command_on_another_binary() -> None:
    command = _get_goto_instrument_unwind_command("swap", goto_file="checking-swap-contracts.goto")
    assert command == (
        "goto-instrument --partial-loops --unwind 5 "
        "checking-swap-contracts.goto checking-swap-contracts.goto"
    )


def test_get_cbmc_check_command() -> None:
    command = _get_cbmc_check_command("swap", 200)
    assert command == "cbmc checking-swap-contracts.goto --function swap --depth 200"
    assert _get_cbmc_check_command("swap", 300).endswith("--function swap --depth 300")


def test_compile_with_goto_cc_returns_zero_for_valid_c(tmp_path: Path) -> None:
    # `no_callees.c` is a small, self-contained valid file used elsewhere in the suite.
    # Run goto-cc inside a tmp dir so the resulting `.goto` doesn't pollute the repo.
    src = Path("test/data/no_callees.c").resolve()
    original_cwd = Path.cwd()
    try:
        os.chdir(tmp_path)
        result = compile_with_goto_cc(function="baz", file_path=str(src))
    finally:
        os.chdir(original_cwd)
    if isinstance(result, CompletedProcess):
        if result.returncode:
            print(f"Error while compiling function baz in {src}")
            print(result.stdout)
            print(result.stderr)
        result.check_returncode()
    assert result == 0


def test_compile_with_goto_cc_returns_nonzero_for_invalid_c(tmp_path: Path) -> None:
    # Pointer addition is illegal in C; goto-cc rejects it. This mirrors what happens
    # when `mutate_function.py` swaps `-` for `+` on two pointers.
    bad_src = tmp_path / "ptr_add.c"
    bad_src.write_text(
        "#include <stddef.h>\nptrdiff_t add_ptrs(int *p, int *q) { return p + q; }\n",
        encoding="utf-8",
    )
    original_cwd = Path.cwd()
    try:
        os.chdir(tmp_path)
        result = compile_with_goto_cc(function="add_ptrs", file_path=str(bad_src))
    finally:
        os.chdir(original_cwd)
    assert result != 0


def test_run_cbmc_returns_success_result_for_trivially_verifying_function(
    tmp_path: Path,
) -> None:
    # `swap` in quicksort.c has a complete contract and no callees — fast to verify and
    # exercises every step of the new per-step pipeline end-to-end.
    src = Path("test/data/quicksort.c").resolve()
    original_cwd = Path.cwd()
    try:
        os.chdir(tmp_path)
        result = run_cbmc(
            function_to_verify="swap", file_containing_function_to_verify=str(src)
        )
    finally:
        os.chdir(original_cwd)
    assert isinstance(result, RunCbmcResult)
    assert result.failed_step is None
    assert result.timed_out is False
    assert result.returncode == 0
    assert result.cbmc_ran_successfully
    assert "swap verified successfully" in result.response


def test_run_cbmc_names_failed_step_for_uncompilable_source(tmp_path: Path) -> None:
    # An invalid C source can't even reach goto-instrument — the failure must be attributed
    # to the goto-cc step.
    bad_src = tmp_path / "ptr_add.c"
    bad_src.write_text(
        "#include <stddef.h>\nptrdiff_t add_ptrs(int *p, int *q) { return p + q; }\n",
        encoding="utf-8",
    )
    original_cwd = Path.cwd()
    try:
        os.chdir(tmp_path)
        result = run_cbmc(
            function_to_verify="add_ptrs",
            file_containing_function_to_verify=str(bad_src),
        )
    finally:
        os.chdir(original_cwd)
    assert result.failed_step is CbmcStep.GOTO_CC
    assert not result.cbmc_ran_successfully
    assert result.returncode != 0


def test_run_command_honours_a_custom_timeout() -> None:
    result = _run_command(CbmcStep.CBMC, "sleep 5", [], timeout_sec=1)
    assert result.timed_out
    assert result.timeout_sec == 1
    assert not result.succeeded


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


def test_format_failure_response_leads_with_failure_lines_for_cbmc_step() -> None:
    # CBMC prints its FAILURE lines and verdict at the tail of stdout. The response must
    # hoist them ahead of stderr/stdout so they survive the harness's head-only preview.
    stdout = (
        "goto-cc banner\n"
        + "symex progress\n" * 50
        + "[f.assertion.1] line 9: FAILURE\n"
        + "** 1 of 12 failed\nVERIFICATION FAILED\n"
    )
    response = _format_failure_response("f", CbmcStep.CBMC, stdout=stdout, stderr="some stderr")

    # FAILURE lines lead, before both stream sections.
    assert response.index("--- FAILURE lines ---") < response.index("--- stderr ---")
    assert response.index("--- FAILURE lines ---") < response.index("--- stdout ---")
    # The hoisted failure line lands in the head, where the inline preview looks.
    assert "[f.assertion.1] line 9: FAILURE" in response[:2000]


def test_format_failure_response_omits_failure_section_for_compile_failure() -> None:
    # goto-cc emits compiler errors on stderr and has no CBMC-style FAILURE lines, so the
    # response leads with stderr rather than an empty FAILURE section.
    response = _format_failure_response(
        "f", CbmcStep.GOTO_CC, stdout="preprocessing...\n", stderr="error: expected ';'\n"
    )
    assert "--- FAILURE lines ---" not in response
    assert response.index("--- stderr ---") < response.index("--- stdout ---")
    assert "failed at goto-cc" in response


def test_format_failure_response_truncates_but_keeps_failures_leading() -> None:
    # A pathological stdout far exceeds the budget; the response must stay bounded while
    # still leading with the (capped) FAILURE lines and switching the streams to tails.
    stdout = "noise\n" * 50_000 + "[f.assertion.7] line 3: FAILURE\n"
    response = _format_failure_response("f", CbmcStep.CBMC, stdout=stdout, stderr="e" * 50_000)

    assert len(response) <= _MAX_RESPONSE_CHARS
    assert response.startswith("f failed to verify with the following errors:")
    assert "[f.assertion.7] line 3: FAILURE" in response[:2000]
    assert "--- stderr (tail) ---" in response
    assert "--- stdout (tail) ---" in response


def test_format_mutation_success_section_renders_kill_score_and_survivor_diff() -> None:
    # `<` -> `<=` survived (spec too weak); `<` -> `>` was killed.
    survivor = _make_mutant(
        source="int f(int a, int b) { return a <= b; }",
        original_operator="<",
        replacement_operator="<=",
        start_byte=31,
        line=1,
    )
    killed = _make_mutant(
        source="int f(int a, int b) { return a > b; }",
        original_operator="<",
        replacement_operator=">",
        start_byte=31,
        line=1,
    )
    score = MutationScore(
        file="quicksort.c",
        target_function="f",
        num_mutants=2,
        num_killed=1,
        num_survived=1,
        num_timed_out=0,
        num_compile_failed=0,
        num_instrumentation_failed=0,
        kill_score=0.5,
        results=[_vresult(killed, killed=True), _vresult(survivor, killed=False)],
    )

    section = get_mutation_testing_results_for_client(score)

    # Kill-score line, formatted to 4 decimals like summary().
    assert "Mutation kill score: 0.5000 (killed 1/2; 1 survived" in section
    # Clickable file:line reference plus operator context for the survivor.
    assert "quicksort.c:1 (RELATIONAL: < -> <=)" in section
    # The survivor's diff body is rendered verbatim.
    assert "+int f(int a, int b) { return a <= b; }" in section
    # The killed mutant is not rendered (its unique `>` perturbation must be absent).
    assert "return a > b" not in section


def test_format_mutation_success_section_reports_all_killed() -> None:
    killed = _make_mutant(
        source="int f(int a, int b) { return a > b; }",
        original_operator="<",
        replacement_operator=">",
        start_byte=31,
        line=1,
    )
    score = MutationScore(
        file="quicksort.c",
        target_function="f",
        num_mutants=1,
        num_killed=1,
        num_survived=0,
        num_timed_out=0,
        num_compile_failed=0,
        num_instrumentation_failed=0,
        kill_score=1.0,
        results=[_vresult(killed, killed=True)],
    )

    section = get_mutation_testing_results_for_client(score)

    assert "Mutation kill score: 1.0000" in section
    assert "All decided mutants were killed." in section
    assert "surviving mutant" not in section


def test_format_mutation_success_section_excludes_undecided_mutants() -> None:
    survivor = _make_mutant(
        source="int f(int a, int b) { return a <= b; }",
        original_operator="<",
        replacement_operator="<=",
        start_byte=31,
        line=1,
    )
    timed_out = MutantVerificationResult(
        mutant=survivor, path_to_mutant="m.c", killed=False, returncode=124, timed_out=True
    )
    compile_failed = MutantVerificationResult(
        mutant=survivor, path_to_mutant="m.c", killed=False, returncode=1, compile_failed=True
    )
    score = MutationScore(
        file="quicksort.c",
        target_function="f",
        num_mutants=2,
        num_killed=0,
        num_survived=0,
        num_timed_out=1,
        num_compile_failed=1,
        num_instrumentation_failed=0,
        kill_score=0.0,
        results=[timed_out, compile_failed],
    )

    # Neither a timed-out nor a compile-failed mutant counts as a survivor.
    assert "All decided mutants were killed." in get_mutation_testing_results_for_client(score)


def test_format_mutation_success_section_bounds_size_and_marks_omissions() -> None:
    # Each survivor's diff is dominated by one ~8 KB line, so a handful of them exceed the
    # section budget and force truncation behind an omission marker.
    big = "x" * 8000
    survivors = [
        _vresult(
            _make_mutant(
                source=f"a<={big}",
                original_operator="<",
                replacement_operator="<=",
                start_byte=1,
                line=1,
            ),
            killed=False,
        )
        for _ in range(20)
    ]
    score = MutationScore(
        file="big.c",
        target_function="f",
        num_mutants=20,
        num_killed=0,
        num_survived=20,
        num_timed_out=0,
        num_compile_failed=0,
        num_instrumentation_failed=0,
        kill_score=0.0,
        results=survivors,
    )

    section = get_mutation_testing_results_for_client(score)

    assert "more surviving mutant(s) omitted" in section
    # The section stays bounded (budget plus a small marker overhead).
    assert len(section) <= _MAX_MUTATION_SECTION_CHARS + 200
    # Not all 20 survivors were rendered.
    assert "surviving mutant 20 —" not in section


def test_print_mutation_progress_reports_running_tally_on_stderr(capsys) -> None:
    mutant = _make_mutant(
        source="x", original_operator="<", replacement_operator=">", start_byte=0, line=1
    )
    # Two killed, one survived so far, out of five total mutants.
    vresults = [
        _vresult(mutant, killed=True),
        _vresult(mutant, killed=True),
        _vresult(mutant, killed=False),
    ]

    _print_mutation_progress(done=3, total=5, vresults=vresults)

    captured = capsys.readouterr()
    # Progress goes to stderr, never stdout, so it cannot contaminate the kill-score result.
    assert captured.out == ""
    assert (
        "[mutation testing] 3/5 done "
        "(killed=2 survived=1 timed_out=0 compile_failed=0 instrumentation_failed=0)" in captured.err
    )


def test_print_mutation_progress_buckets_undecided_mutants(capsys) -> None:
    mutant = _make_mutant(
        source="x", original_operator="<", replacement_operator=">", start_byte=0, line=1
    )
    timed_out = MutantVerificationResult(
        mutant=mutant, path_to_mutant="m.c", killed=False, returncode=124, timed_out=True
    )
    compile_failed = MutantVerificationResult(
        mutant=mutant, path_to_mutant="m.c", killed=False, returncode=1, compile_failed=True
    )

    _print_mutation_progress(done=2, total=2, vresults=[timed_out, compile_failed])

    # Timed-out and compile-failed mutants are bucketed separately, not counted as survived,
    # mirroring `_aggregate_mutation_score`.
    assert (
        "[mutation testing] 2/2 done "
        "(killed=0 survived=0 timed_out=1 compile_failed=1 instrumentation_failed=0)" in capsys.readouterr().err
    )


def test_run_cbmc_records_the_depth_it_was_given_and_passes_it_to_cbmc(tmp_path: Path) -> None:
    src = tmp_path / "quicksort.c"
    src.write_bytes(Path("test/data/quicksort.c").read_bytes())
    deeper = run_cbmc("swap", str(src), depth=300)
    assert deeper.cbmc_ran_successfully and deeper.depth == 300
    default = run_cbmc("swap", str(src))
    assert default.cbmc_ran_successfully and default.depth == 200
    runs = [json.loads(line) for line in (tmp_path / "quicksort-cbmc-runs.jsonl").read_text().splitlines()]
    assert [run["depth"] for run in runs] == [300, 200]
    commands = [step["command"] for run in runs for step in run["subprocess_results"] if step["step"] == "cbmc"]
    assert [command.rsplit(" ", 1)[1] for command in commands] == ["300", "200"]


ALLOCATING_SOURCE = """#include <stdlib.h>
struct abuf { char *b; int len; };

char *mk(int n)
__CPROVER_requires(n > 0 && n < 8)
__CPROVER_ensures(__CPROVER_return_value == NULL || __CPROVER_r_ok(__CPROVER_return_value, n))
{
    return malloc(n);
}

void abFree(struct abuf *ab)
__CPROVER_requires(__CPROVER_is_fresh(ab, sizeof(struct abuf)))
__CPROVER_assigns()
{
    free(ab->b);
}
"""


def test_the_library_is_added_after_the_contract_is_enforced(tmp_path: Path) -> None:
    # goto-instrument aborts when it enforces a contract on a binary that already holds the
    # library's `malloc` and `free` models. The missing-body retry adds the library afterwards,
    # so the run ends in a verdict, not a crash.
    source = tmp_path / "alloc.c"
    source.write_text(ALLOCATING_SOURCE, encoding="utf-8")
    results = {
        function: run_cbmc(
            function_to_verify=function,
            file_containing_function_to_verify=str(source),
        )
        for function in ("mk", "abFree")
    }
    assert results["mk"].cbmc_ran_successfully
    # abFree's contract leaves out the freed object, so the run fails in cbmc, not before.
    assert results["abFree"].failed_step is CbmcStep.CBMC
