"""Claude Code tool to get the command to run CBMC."""

# ruff: noqa

from typing import Any

from claude_agent_sdk import tool


@tool(
    name="get_cbmc_command",
    description="Run CBMC with loop unwinding = 5, depth = 100",
    input_schema={
        "function_to_verify": str,
        "stub_file_paths": list[str],
        "file_containing_function_to_verify": list[str],
        "callee_functions": list[str],
    },
)
async def get_cbmc_command(args: dict[str, Any]) -> dict[str, Any]:
    return {"content": _get_cbmc_command(args)}


def _get_cbmc_command(args: dict[str, Any]) -> str:
    """Return the command that should be used to verify a function in a C file with CBMC.

    The command will run CBMC with a loop unrolling bound of 5, and a symbolic exploration depth of
    100 along execution paths.

    Args:
        args (dict[str, Any]): The map containing the arguments to populate the CBMC command with.

    Returns:
        dict[str, Any]: A map containing the CBMC command that should be used by Claude.
    """
    function_to_verify = args["function_to_verify"]
    stub_file_paths = args["stub_file_paths"]
    file_containing_function_to_verify = args["file_containing_function_to_verify"]
    callee_functions = args["callee_functions"]
    return " && ".join(
        [
            (
                f"goto-cc -o {function_to_verify}.goto"
                f"{' ' + stub_file_paths if stub_file_paths else ''} "
                f"{file_containing_function_to_verify} "
                f"{function_to_verify} "
                f"--function {function_to_verify}"
            ),
            (
                f"goto-instrument --partial-loops --unwind 5 "
                f"{function_to_verify}.goto {function_to_verify}.goto"
            ),
            (
                f"goto-instrument"
                f"{' ' + ' '.join(callee_functions) if callee_functions else ''} "
                f"--enforce-contract {function_to_verify} "
                f"{function_to_verify}.goto checking-{function_to_verify}-contracts.goto"
            ),
            (
                f"cbmc checking-{function_to_verify}-contracts.goto "
                f"--function {function_to_verify} --depth 100"
            ),
        ]
    )
