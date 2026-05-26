"""Tests for the run_cbmc tool."""

import os
from pathlib import Path

from tools.run_cbmc import compile_with_goto_cc, get_cbmc_command


def test_get_cbmc_command_no_callees_no_stubs() -> None:
    command = get_cbmc_command(
        function_to_verify="swap",
        callees=[],
        file_containing_function="quicksort.c",
    )
    assert command == (
        "goto-cc -o swap.goto quicksort.c --function swap && "
        "goto-instrument --partial-loops --unwind 5 swap.goto swap.goto && "
        "goto-instrument --enforce-contract swap swap.goto checking-swap-contracts.goto && "
        "cbmc checking-swap-contracts.goto --function swap --depth 100"
    )


def test_get_cbmc_command_includes_self_for_inductive_recursive_verification() -> None:
    command = get_cbmc_command(
        function_to_verify="quickSort",
        callees=["partition", "quickSort"],
        file_containing_function="test/data/quicksort.c",
    )
    assert "--replace-call-with-contract quickSort" in command
    assert "--enforce-contract quickSort" in command


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
        "#include <stddef.h>\n"
        "ptrdiff_t add_ptrs(int *p, int *q) { return p + q; }\n",
        encoding="utf-8",
    )
    original_cwd = Path.cwd()
    try:
        os.chdir(tmp_path)
        returncode = compile_with_goto_cc(function="add_ptrs", file_path=str(bad_src))
    finally:
        os.chdir(original_cwd)
    assert returncode != 0
