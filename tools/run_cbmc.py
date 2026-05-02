"""Tool to run CBMC on a given function."""

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from tools.avocado_tool_registry import mcp
from tools.util import (
    build_stub_index,
    get_in_file_callees_for,
    get_unstubbed_external_callees_for,
)

# The stub index is built once on MCP server start.
_STUB_INDEX = build_stub_index()


@mcp.tool()
def run_cbmc(
    function_to_verify: str,
    file_containing_function_to_verify: str,
    path_to_call_graph: str,
) -> str:
    """Run CBMC on the given function with loop unwinding = 5, depth = 100.

    Args:
        function_to_verify (str): Name of the function to verify.
        file_containing_function_to_verify (str): Path to the C file defining the function.
        path_to_call_graph (str): Path to the JSON call graph produced by `construct_call_graph`.

    Returns:
        The combined stdout and stderr produced by the CBMC pipeline.
    """
    callees = get_in_file_callees_for(function_to_verify, path_to_call_graph)
    nondet_callees = get_unstubbed_external_callees_for(
        function_to_verify, path_to_call_graph, _STUB_INDEX
    )
    cbmc_command = _get_cbmc_command(
        function_to_verify,
        callees,
        file_containing_function_to_verify,
    )
    result = subprocess.run(cbmc_command, capture_output=True, text=True, shell=True, check=False)
    _log_invocation(
        file_containing_function_to_verify,
        function_to_verify,
        cbmc_command,
        result.returncode,
        nondet_callees,
    )
    if result.returncode == 0:
        return f"{function_to_verify} verified successfully"
    error_lines = [line for line in result.stdout.split("\n") if "FAILURE" in line]
    if not error_lines:
        return f"{function_to_verify} failed to verify"
    return (
        f"{function_to_verify} failed to verify with the following errors:\n\n"
        f"{'\n'.join(error_lines)}"
    )


def _log_invocation(
    file_under_verification: str,
    function: str,
    command: str,
    returncode: int,
    nondet_callees: list[str],
) -> None:
    log_path = Path(f"{Path(file_under_verification).stem}-cbmc-runs.jsonl")
    record = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "function": function,
        "file": file_under_verification,
        "command": command,
        "returncode": returncode,
        "nondet_callees": nondet_callees,
    }
    try:
        with log_path.open("a") as f:
            f.write(json.dumps(record) + "\n")
    except OSError:
        pass


def _get_cbmc_command(
    function_to_verify: str,
    callees: list[str],
    file_containing_function: str,
) -> str:
    """Return the command that should be used to verify a function in a C file with CBMC.

    The command will run CBMC with a loop unrolling bound of 5, and a symbolic exploration depth of
    100 along execution paths.

    Args:
        function_to_verify (str): The function to verify.
        callees (list[str]): The callees of the function to verify.
        file_containing_function (str): The path to the file containing the function to verify.

    Returns:
        str: The CBMC command that should be used by Claude.
    """
    replace_calls = "".join(f" --replace-call-with-contract {c}" for c in callees)
    return " && ".join(
        [
            (
                f"goto-cc -o {function_to_verify}.goto "
                f"{file_containing_function} "
                f"--function {function_to_verify}"
            ),
            (
                f"goto-instrument --add-library --partial-loops --unwind 5 "
                f"{function_to_verify}.goto {function_to_verify}.goto"
            ),
            (
                f"goto-instrument{replace_calls} "
                f"--enforce-contract {function_to_verify} "
                f"{function_to_verify}.goto checking-{function_to_verify}-contracts.goto"
            ),
            (
                f"cbmc checking-{function_to_verify}-contracts.goto "
                f"--function {function_to_verify} --depth 100"
            ),
        ]
    )
