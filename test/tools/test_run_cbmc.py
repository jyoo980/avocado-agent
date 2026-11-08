"""Tests for the run_cbmc tool."""

import json
import os
import shutil
from pathlib import Path
from subprocess import CompletedProcess

from eval.mutants.mutate_function import Mutant
from tools import run_cbmc as run_cbmc_module
from tools.run_cbmc import (
    CbmcStep,
    PipelineVariant,
    RunCbmcResult,
    _MAX_RESPONSE_CHARS,
    _drop_missing_body_warnings,
    _failure_lines_section,
    _format_failure_response,
    _get_cbmc_check_command,
    _get_goto_cc_command,
    _get_goto_instrument_add_library_command,
    _get_goto_instrument_contract_command,
    _get_goto_instrument_unwind_command,
    _get_tail_within_budget,
    _run_command,
    _tail_section,
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
    command = _get_goto_instrument_unwind_command("swap.goto")
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


def test_get_goto_instrument_add_library_command() -> None:
    command = _get_goto_instrument_add_library_command("checking-swap-contracts.goto")
    assert command == (
        "goto-instrument --add-library checking-swap-contracts.goto checking-swap-contracts.goto"
    )


def test_drop_missing_body_warnings_keeps_other_lines() -> None:
    output = (
        "Reading GOTO program from 'swap.goto'\n"
        "file swap.c line 3 function swap: no body for function 'malloc'\n"
        "Writing GOTO program to 'checking-swap-contracts.goto'\n"
    )
    assert _drop_missing_body_warnings(output) == (
        "Reading GOTO program from 'swap.goto'\n"
        "Writing GOTO program to 'checking-swap-contracts.goto'\n"
    )


def test_get_cbmc_check_command() -> None:
    command = _get_cbmc_check_command("swap")
    assert command == "cbmc checking-swap-contracts.goto --function swap --depth 200"


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


def test_run_command_timeout_kills_the_shells_children(tmp_path: Path) -> None:
    # The shell forks `sleep` (and here also backgrounds a second one); killing only the shell
    # on timeout would orphan them, leaving e.g. a CBMC solver running after its mutant timed out.
    pid_file = tmp_path / "pids"
    command = f"sleep 60 & echo $! > {pid_file}; sleep 60; true"
    result = _run_command(CbmcStep.CBMC, command, [], timeout_sec=1)
    assert result.timed_out
    background_pid = int(pid_file.read_text().strip())
    # A killed orphan may linger briefly as an unreaped zombie ("Z"); that counts as dead.
    try:
        stat = Path(f"/proc/{background_pid}/stat").read_text()
    except FileNotFoundError:
        return
    state = stat.rsplit(")", 1)[1].split()[0]
    if state != "Z":
        os.kill(background_pid, 9)
        raise AssertionError(f"child of the timed-out shell is still running (state {state})")

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


def test_a_result_records_the_pipeline_variant_that_produced_it(
    tmp_path: Path, monkeypatch
) -> None:
    src = tmp_path / "quicksort.c"
    shutil.copy(Path("test/data/quicksort.c"), src)
    result = run_cbmc("swap", str(src))
    assert result.is_function_verified, result.response
    assert result.variant == PipelineVariant()

    # Given a variant, `run_cbmc` runs the pipeline once, in that variant, and does not retry.
    macro_expansion_flags: list[bool] = []
    run_pipeline = run_cbmc_module._run_pipeline

    def recording_run_pipeline(*args, **kwargs):
        macro_expansion_flags.append(kwargs["prevent_macro_expansion"])
        return run_pipeline(*args, **kwargs)

    monkeypatch.setattr(run_cbmc_module, "_run_pipeline", recording_run_pipeline)
    variant = PipelineVariant(prevent_macro_expansion=True)
    assert run_cbmc("swap", str(src), variant=variant).variant == variant
    assert macro_expansion_flags == [True]


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


def test_format_failure_response_keeps_end_of_stdout_when_streams_are_huge() -> None:
    # Each dropped count has more digits than `_MAX_RESPONSE_CHARS`; the response must still
    # fit the budget without clipping the end of the stdout tail.
    stdout = "noise\n" * 400_000 + "VERIFICATION FAILED\n"
    response = _format_failure_response("f", CbmcStep.CBMC, stdout=stdout, stderr="e\n" * 1_200_000)

    assert len(response) <= _MAX_RESPONSE_CHARS
    assert response.endswith("VERIFICATION FAILED\n\n")


def test_format_failure_response_gives_stdout_the_budget_that_stderr_does_not_need() -> None:
    stdout = "x" * 200_000 + "VERIFICATION FAILED\n"
    response = _format_failure_response("f", CbmcStep.CBMC, stdout=stdout, stderr="short\n")

    assert _MAX_RESPONSE_CHARS - 100 <= len(response) <= _MAX_RESPONSE_CHARS
    assert response.endswith("VERIFICATION FAILED\n\n")


def test_format_failure_response_gives_stderr_the_budget_that_stdout_does_not_need() -> None:
    stderr = "e" * 200_000 + "END OF STDERR\n"
    response = _format_failure_response("f", CbmcStep.CBMC, stdout="short\n", stderr=stderr)

    assert _MAX_RESPONSE_CHARS - 100 <= len(response) <= _MAX_RESPONSE_CHARS
    assert "END OF STDERR\n" in response
    assert response.endswith("--- stdout (tail) ---\nshort\n\n")


def test_tail_section_fits_budget_and_keeps_end_of_content() -> None:
    section = _tail_section("stdout (tail)", "x" * 1000 + "END", 100)

    assert len(section) <= 100
    assert section.endswith("END\n")


def test_tail_section_with_zero_budget_has_empty_tail() -> None:
    assert (
        _tail_section("stdout (tail)", "abc", 0)
        == "--- stdout (tail) ---\n[... 3 characters truncated ...]\n\n"
    )


def _function_name_leaving_budget(stdout: str, budget: int) -> str:
    """Return a function name that leaves `budget` chars for the stream sections of a CBMC failure.

    Args:
        stdout (str): The stdout of the failure, which determines the FAILURE section.
        budget (int): The number of chars to leave after the header and the FAILURE section.

    Returns:
        str: A function name of the length that leaves exactly `budget` chars.
    """
    header_without_name = len(" failed to verify with the following errors:\n\n")
    used = header_without_name + len(_failure_lines_section(stdout))
    return "f" * (_MAX_RESPONSE_CHARS - used - budget)


def test_format_failure_response_omits_stderr_when_both_sections_do_not_fit() -> None:
    # A capped FAILURE section and a long function name leave room for stdout's shortest
    # section but not for stderr's as well. The end of the stdout tail must survive.
    stdout = "[f.assertion.1] line 3: FAILURE\n" * 5_000 + "VERIFICATION FAILED\n"
    stderr = "e" * 200_000
    stdout_minimum = len(_tail_section("stdout (tail)", stdout, 0))
    function = _function_name_leaving_budget(stdout, stdout_minimum + 30)

    response = _format_failure_response(function, CbmcStep.CBMC, stdout=stdout, stderr=stderr)

    assert len(response) <= _MAX_RESPONSE_CHARS
    assert "--- stderr (tail) ---" not in response
    assert "--- stdout (tail) ---" in response
    assert response.endswith("FAILED\n\n")


def test_format_failure_response_omits_both_streams_when_neither_section_fits() -> None:
    stdout = "[f.assertion.1] line 3: FAILURE\n" * 5_000 + "VERIFICATION FAILED\n"
    function = _function_name_leaving_budget(stdout, 10)

    response = _format_failure_response(function, CbmcStep.CBMC, stdout=stdout, stderr="e" * 10)

    assert len(response) <= _MAX_RESPONSE_CHARS
    assert "--- stderr" not in response
    assert "--- stdout" not in response
    assert response.endswith("line 3: FAILURE\n")


def test_format_failure_response_keeps_both_sections_when_both_shortest_fit() -> None:
    stdout = "[f.assertion.1] line 3: FAILURE\n" * 5_000 + "VERIFICATION FAILED\n"
    stderr = "e" * 200_000
    minimums = len(_tail_section("stdout (tail)", stdout, 0)) + len(
        _tail_section("stderr (tail)", stderr, 0)
    )
    function = _function_name_leaving_budget(stdout, minimums)

    response = _format_failure_response(function, CbmcStep.CBMC, stdout=stdout, stderr=stderr)

    assert len(response) <= _MAX_RESPONSE_CHARS
    assert "--- stderr (tail) ---" in response
    assert response.endswith(
        f"--- stdout (tail) ---\n[... {len(stdout)} characters truncated ...]\n\n"
    )


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


def test_get_tail_within_budget_returns_short_content_unchanged() -> None:
    assert _get_tail_within_budget("a\nb\n", 10) == ("a\nb\n", 0)


def test_get_tail_within_budget_drops_short_partial_leading_line() -> None:
    content = "line one\n" * 10
    tail, dropped = _get_tail_within_budget(content, 25)

    assert tail == "line one\nline one\n"
    assert dropped == len(content) - len(tail)


def test_get_tail_within_budget_keeps_first_line_when_window_starts_at_line_boundary() -> None:
    assert _get_tail_within_budget("aaa\nbbb\nccc\n", 8) == ("bbb\nccc\n", 4)


def test_get_tail_within_budget_keeps_single_long_line() -> None:
    # The only newline in the window is the trailing one; trimming to it would keep nothing.
    content = "x" * 40_000 + "\n"
    tail, dropped = _get_tail_within_budget(content, 15_000)

    assert tail == content[-15_000:]
    assert dropped == 25_001


def test_get_tail_within_budget_keeps_long_partial_line_before_short_last_line() -> None:
    content = "x" * 40_000 + "\nshort\n"
    tail, _ = _get_tail_within_budget(content, 15_000)

    assert tail == content[-15_000:]


def test_get_tail_within_budget_drops_partial_line_under_half_of_odd_budget() -> None:
    # The window is "bb\ncc"; its partial line "bb" is 2 characters, less than half of 5.
    assert _get_tail_within_budget("aaaaa\nbbbb\ncc", 5) == ("cc", 11)


def test_get_tail_within_budget_zero_budget_keeps_nothing() -> None:
    assert _get_tail_within_budget("abc\n", 0) == ("", 4)


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
    # The library models `free`, so the response does not tell the agent that `free` lacks a body.
    assert "no body for function" not in results["abFree"].response
    # abFree's verdict comes from the missing-body retry, which adds the library to the
    # instrumented binary after enforcing the contract. (mk verifies on the first attempt, without
    # the retry.)
    log_lines = (tmp_path / "alloc-cbmc-runs.jsonl").read_text(encoding="utf-8").splitlines()
    commands_by_function = {
        record["function"]: [run["command"] for run in record["subprocess_results"]]
        for record in map(json.loads, log_lines)
    }
    abfree_commands = commands_by_function["abFree"]
    retry_start = max(i for i, c in enumerate(abfree_commands) if c.startswith("goto-cc"))
    assert abfree_commands[retry_start + 1 :] == [
        "goto-instrument --partial-loops --unwind 5 abFree.goto abFree.goto",
        "goto-instrument --enforce-contract abFree abFree.goto checking-abFree-contracts.goto",
        "goto-instrument --add-library checking-abFree-contracts.goto checking-abFree-contracts.goto",
        "goto-instrument --partial-loops --unwind 5 "
        "checking-abFree-contracts.goto checking-abFree-contracts.goto",
        "cbmc checking-abFree-contracts.goto --function abFree --depth 200",
    ]
