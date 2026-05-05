"""Tests for the run_cbmc tool."""

from tools.run_cbmc import get_cbmc_command


def test__get_cbmc_command_no_callees_no_stubs() -> None:
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


def test__get_cbmc_command_includes_self_for_inductive_recursive_verification() -> None:
    command = get_cbmc_command(
        function_to_verify="quickSort",
        callees=["partition", "quickSort"],
        file_containing_function="test/data/quicksort.c",
    )
    assert "--replace-call-with-contract quickSort" in command
    assert "--enforce-contract quickSort" in command
