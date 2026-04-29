"""Tests for the get_cbmc_command tool."""

from tools.get_cbmc_command import _get_cbmc_command


def test_get_cbmc_command_no_callees_no_stubs() -> None:
    args = {
        "function_to_verify": "swap",
        "stub_file_paths": [],
        "file_containing_function_to_verify": "quicksort.c",
        "callee_functions": [],
    }
    assert _get_cbmc_command(args) == (
        "goto-cc -o swap.goto quicksort.c swap --function swap && "
        "goto-instrument --partial-loops --unwind 5 swap.goto swap.goto && "
        "goto-instrument --enforce-contract swap swap.goto checking-swap-contracts.goto && "
        "cbmc checking-swap-contracts.goto --function swap --depth 100"
    )
