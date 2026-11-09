"""Tests for the run_cbmc tool."""

import json
import os
import shutil
from pathlib import Path
from subprocess import CompletedProcess

import pytest

from tools import run_cbmc as run_cbmc_module
from tools.run_cbmc import (
    CbmcStep,
    PipelineVariant,
    RunCbmcResult,
    _MAX_RESPONSE_CHARS,
    _drop_missing_body_warnings,
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
    # Pointer addition is illegal in C; goto-cc rejects it.
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
    # on timeout would orphan them, leaving e.g. a CBMC solver running after its run timed out.
    pid_file = tmp_path / "pids"
    command = f"sleep 60 & echo $! > {pid_file}; sleep 60; true"
    result = _run_command(CbmcStep.CBMC, command, [], timeout_sec=1)
    assert result.timed_out
    background_pid = int(pid_file.read_text().strip())
    # `_run_command` returns only after every killed process has exited. A killed orphan can
    # linger as an unreaped zombie ("Z"); that counts as exited.
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

def test_run_cbmc_compiles_a_symlinked_source_with_the_headers_beside_the_symlink(
    tmp_path: Path,
) -> None:
    # The target of the symlink has no `seven.h` beside it; the symlink does.
    target = tmp_path / "elsewhere" / "seven.c"
    target.parent.mkdir()
    target.write_text('#include "seven.h"\nint seven(void) { return SEVEN; }\n', encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    (project / "seven.h").write_text("#define SEVEN 7\n", encoding="utf-8")
    src = project / "seven.c"
    try:
        src.symlink_to(target)
    except OSError as e:
        pytest.skip(f"cannot create symlinks: {e}")
    result = run_cbmc("seven", str(src))
    assert result.failed_step is not CbmcStep.GOTO_CC, result.response

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
