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
        "callee_functions": list[str],
    },
)
async def get_cbmc_command(args: dict[str, Any]) -> dict[str, Any]:
    function_to_verify = args["function_to_verify"]
    stub_file_paths = args["stub_file_paths"]
    callee_functions = args["callee_functions"]
    replace_call_with_contract_args = " ".join(callee_functions) if callee_functions else ""
    cbmc_command = " && ".join(
        [
            (
                f"goto-cc -o {function_to_verify}.goto "
                f"{stub_file_paths} "
                f"{function_to_verify} "
                f"--function {function_to_verify}"
            ),
            (
                f"goto-instrument --partial-loops --unwind 5 "
                f"{function_to_verify}.goto {function_to_verify}.goto"
            ),
            (
                f"goto-instrument "
                f"{
                    ' ' + replace_call_with_contract_args if replace_call_with_contract_args else ''
                } "
                f"--enforce-contract {function_to_verify} "
                f"{function_to_verify}.goto checking-{function_to_verify}-contracts.goto"
            ),
            (
                f"cbmc checking-{function_to_verify}-contracts.goto "
                f"--function {function_to_verify} --depth 100"
            ),
        ]
    )
    return {"content": cbmc_command}
