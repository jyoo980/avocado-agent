"""Tests for the run_cbmc tool."""

from tools.run_cbmc import _get_cbmc_command


def test__get_cbmc_command_no_callees_no_stubs() -> None:
    command = _get_cbmc_command(
        function_to_verify="swap",
        callees=[],
        file_containing_function="quicksort.c",
    )
    assert command == (
        "goto-cc -o swap.goto quicksort.c swap --function swap && "
        "goto-instrument --add-library --partial-loops --unwind 5 swap.goto swap.goto && "
        "goto-instrument --enforce-contract swap swap.goto checking-swap-contracts.goto && "
        "cbmc checking-swap-contracts.goto --function swap --depth 100"
    )
