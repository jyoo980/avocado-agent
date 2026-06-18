"""Tests for the run_cbmc tool."""

import os
from pathlib import Path

from eval.mutants.mutate_function import Mutant
from tools.run_cbmc import (
    CbmcStep,
    RunCbmcResult,
    _get_cbmc_check_command,
    _get_goto_cc_command,
    _get_goto_instrument_add_library_command,
    _get_goto_instrument_contract_command,
    _get_goto_instrument_unwind_command,
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
    assert command == "goto-cc -o swap.goto quicksort.c --function swap"


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


def test_get_goto_instrument_add_library_command() -> None:
    command = _get_goto_instrument_add_library_command("swap")
    assert command == "goto-instrument --add-library swap.goto swap.goto"


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
        returncode = compile_with_goto_cc(function="baz", file_path=str(src))
    finally:
        os.chdir(original_cwd)
    assert returncode == 0


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
        returncode = compile_with_goto_cc(function="add_ptrs", file_path=str(bad_src))
    finally:
        os.chdir(original_cwd)
    assert returncode != 0


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
        "(killed=2 survived=1 timed_out=0 compile_failed=0)" in captured.err
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
        "(killed=0 survived=0 timed_out=1 compile_failed=1)" in capsys.readouterr().err
    )
