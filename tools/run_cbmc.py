"""Stdio MCP server exposing a `run_cbmc` tool to Claude Code."""

import subprocess

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(name="run_cbmc")


@mcp.tool()
def run_cbmc(
    function_to_verify: str,
    # stub_file_paths: list[str],
    file_containing_function_to_verify: str,
) -> str:
    """Run CBMC on the given function with loop unwinding = 5, depth = 100.

    Returns:
        The combined stdout and stderr produced by the CBMC pipeline.
    """
    # TODO: Automatically determine the stub file paths and the callees.
    callees = []
    stub_file_paths = []
    cbmc_command = _get_cbmc_command(
        function_to_verify,
        stub_file_paths,
        callees,
        file_containing_function_to_verify,
    )
    result = subprocess.run(cbmc_command, capture_output=True, text=True, shell=True, check=False)
    if result.returncode == 0:
        return f"{function_to_verify} verified successfully"
    # Limit output, somehow?
    return f"{function_to_verify} failed to verify"


def _get_cbmc_command(
    function_to_verify: str,
    stub_file_paths: list[str],
    callees: list[str],
    file_containing_function: str,
) -> str:
    """Return the command that should be used to verify a function in a C file with CBMC.

    The command will run CBMC with a loop unrolling bound of 5, and a symbolic exploration depth of
    100 along execution paths.

    Args:
        function_to_verify (str): The function to verify.
        stub_file_paths (list[str]): The list of stub files to pass to CBMC.
        callees (list[str]): The callees of the function to verify.
        file_containing_function (str): The path to the file containing the function to verify.

    Returns:
        str: The CBMC command that should be used by Claude.
    """
    replace_calls = "".join(f" --replace-call-with-contract {c}" for c in callees)
    return " && ".join(
        [
            (
                f"goto-cc -o {function_to_verify}.goto"
                f"{' ' + ' '.join(stub_file_paths) if stub_file_paths else ''} "
                f"{file_containing_function} "
                f"{function_to_verify} "
                f"--function {function_to_verify}"
            ),
            (
                f"goto-instrument --partial-loops --unwind 5 "
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


if __name__ == "__main__":
    mcp.run(transport="stdio")
