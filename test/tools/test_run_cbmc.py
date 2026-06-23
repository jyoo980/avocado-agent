"""Tests for the run_cbmc tool."""

import os
from pathlib import Path

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
    compile_with_goto_cc,
    run_cbmc,
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

