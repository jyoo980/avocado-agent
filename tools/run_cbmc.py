"""Run CBMC on a function."""
# [[MDE: Here and elsewhere, document usage.]]

import argparse
import json
import shlex
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from tools.util import (
    build_stub_index,
    get_in_file_callees_for,
    get_unstubbed_external_callees_for,
)
from tools.util.callgraph import CallGraph

# Char budget for failure responses, sized to keep CLI output bounded so it
# doesn't exceed an agent's tool-output limits.
_MAX_RESPONSE_CHARS = 100_000

# Of the output size budget left after the header, FAILURE lines, and section labels, this
# fraction is given to the stdout tail; the remainder goes to the stderr tail.
_STDOUT_TAIL_SHARE = 0.7


def run_cbmc(
    function_to_verify: str,
    file_containing_function_to_verify: str,
    path_to_call_graph: str,
    replace_recursive_calls: bool = False,
    stub_index: dict | None = None,
) -> tuple[str, int]:
    """Run CBMC on the given function with loop unwinding = 5, depth = 100.

    Args:
        function_to_verify (str): Name of the function to verify.
        file_containing_function_to_verify (str): Path to the C file defining the function.
        path_to_call_graph (str): Path to the JSON call graph produced by `construct_call_graph`.
        replace_recursive_calls (bool): Set to True when verifying a self-recursive function so
            the recursive call is discharged by the function's own contract (induction) instead
            of being handled by `--unwind`. The contract must be inductive — strong enough to
            imply itself at the recursive call site — or the proof will be vacuous. Defaults to
            False.
        stub_index (dict | None): Index of stub functions to their files, defaults to None.

    Returns:
        A (response_text, returncode) tuple. The text is a success message or a
        truncated failure block; the returncode is CBMC's exit code (0 on success).
    """
    if stub_index is None:
        stub_index = build_stub_index()
    call_graph = CallGraph(json.loads(Path(path_to_call_graph).read_text(encoding="utf-8")))
    callees = get_in_file_callees_for(
        function_to_verify, call_graph, include_self=replace_recursive_calls
    )
    nondet_callees = get_unstubbed_external_callees_for(function_to_verify, call_graph, stub_index)
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
        return (f"{function_to_verify} verified successfully", 0)
    return (
        _format_failure_response(function_to_verify, result.stdout, result.stderr),
        result.returncode,
    )


def _format_failure_response(function: str, stdout: str, stderr: str) -> str:
    """Format a CBMC failure response, truncating only if it exceeds the char budget.

    When the combined labeled output fits within `_MAX_RESPONSE_CHARS`, both streams are
    returned in full. Otherwise, FAILURE lines from stdout are preserved and the rest of
    each stream is replaced by its tail, with an explicit truncation marker.

    Args:
        function (str): The name of the function that failed verification.
        stdout (str): The stdout content for the CBMC process.
        stderr (str): The stderr content for the CBMC process.

    Returns:
        str: The formatted CBMC failure response, truncated iff it has exceeded the char budget.
    """
    header = f"{function} failed to verify with the following errors:\n\n"
    full = f"{header}--- stderr ---\n{stderr}\n--- stdout ---\n{stdout}"
    if len(full) <= _MAX_RESPONSE_CHARS:
        return full

    failure_lines = [line for line in stdout.split("\n") if "FAILURE" in line]
    failure_block = "\n".join(failure_lines)
    # Cap the FAILURE block at half the total budget so a pathological run with
    # tens of thousands of FAILURE lines can't blow past the limit on its own.
    failure_cap = _MAX_RESPONSE_CHARS // 2
    if len(failure_block) > failure_cap:
        dropped = len(failure_block) - failure_cap
        failure_block = (
            f"[... {dropped} characters of FAILURE lines truncated ...]\n"
            f"{failure_block[-failure_cap:]}"
        )

    # Reserve space for headers, section labels, and truncation markers. The
    # `_MAX_RESPONSE_CHARS`-wide placeholder pads the digit count so the actual
    # marker (with the real dropped count) cannot push us over budget.
    digit_pad = str(_MAX_RESPONSE_CHARS)
    fixed = (
        f"{header}"
        f"--- stderr (tail) ---\n[... {digit_pad} characters truncated ...]\n\n"
        f"--- stdout (FAILURE lines) ---\n{failure_block}\n"
        f"--- stdout (tail) ---\n[... {digit_pad} characters truncated ...]\n"
    )
    remaining = max(_MAX_RESPONSE_CHARS - len(fixed), 0)
    stdout_budget = int(remaining * _STDOUT_TAIL_SHARE)
    stderr_budget = remaining - stdout_budget

    stderr_section = _tail_section("stderr (tail)", stderr, stderr_budget)
    stdout_tail_section = _tail_section("stdout (tail)", stdout, stdout_budget)

    response = (
        f"{header}"
        f"{stderr_section}\n"
        f"--- stdout (FAILURE lines) ---\n{failure_block}\n"
        f"{stdout_tail_section}"
    )
    # Hard clamp: the per-section budget accounting can drift by a few chars
    # against the `fixed` estimate, so guarantee we never exceed the cap.
    return response[:_MAX_RESPONSE_CHARS]


def _tail_section(label: str, content: str, budget: int) -> str:
    """Render a labeled section containing the tail of content within budget chars.

    Args:
        label (str): The label of the section.
        content (str): The content to truncate.
        budget (int): The maximum number of characters to include in the section.

    Returns:
        str: The labeled section containing the tail of content within budget chars.
    """
    if len(content) <= budget:
        body = content
    else:
        dropped = len(content) - budget
        body = f"[... {dropped} characters truncated ...]\n{content[-budget:]}"
    return f"--- {label} ---\n{body}\n"


def _log_invocation(
    file_under_verification: str,
    function: str,
    command: str,
    returncode: int,
    nondet_callees: list[str],
) -> None:
    """Log a CBMC invocation with the given arguments.

    Args:
        file_under_verification (str): The file that contains the function under verification.
        function (str): The function under verification.
        command (str): The CBMC command used to verify the function.
        returncode (int): The return code of the CBMC command used to verify the function.
        nondet_callees (list[str]): The list of callees that CBMC treated as non-deterministic
            during verification.
    """
    source_path = Path(file_under_verification)
    log_path = source_path.with_name(f"{source_path.stem}-cbmc-runs.jsonl")
    record = {
        "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
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
        # This does fail silently, but it shouldn't stop the tool from making progress.
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
    quoted_function_to_verify = shlex.quote(function_to_verify)
    return " && ".join(
        [
            (
                f"goto-cc -o {quoted_function_to_verify}.goto "
                f"{shlex.quote(file_containing_function)} "
                f"--function {quoted_function_to_verify}"
            ),
            (
                f"goto-instrument --partial-loops --unwind 5 "
                f"{quoted_function_to_verify}.goto {quoted_function_to_verify}.goto"
            ),
            (
                f"goto-instrument{replace_calls} "
                f"--enforce-contract {quoted_function_to_verify} "
                f"{quoted_function_to_verify}.goto "
                f"checking-{quoted_function_to_verify}-contracts.goto"
            ),
            (
                f"cbmc checking-{quoted_function_to_verify}-contracts.goto "
                f"--function {quoted_function_to_verify} --depth 100"
            ),
        ]
    )


# [[MDE: As a general rule, please put `main()` first in a file.  That makes the file easier to
# understand.]]
def main() -> None:
    """Run CBMC on a function."""
    parser = argparse.ArgumentParser(
        description=(
            "Run CBMC on a function with loop unwinding = 5, depth = 100. "
            "Exits with status 0 on verification success and 1 on verification failure."
            # [[MDE: Why guarantee status 1 on verification failure?  Returning the actual return
            # code may provide more information (it cannot hurt, so far as I can see).]]
        )
    )
    parser.add_argument("--function", required=True, help="Name of the function to verify.")
    parser.add_argument("--file", required=True, help="Path to the C file defining the function.")
    parser.add_argument(
        "--call-graph",
        required=True,
        help="Path to the JSON call graph produced by avocado-construct-call-graph.",
    )
    parser.add_argument(
        "--replace-recursive-calls",
        action="store_true",
        help=(
            "Set when verifying a self-recursive function so the recursive call is discharged "
            "by the function's own contract (induction) instead of --unwind."
        ),
    )
    args = parser.parse_args()
    response, returncode = run_cbmc(
        function_to_verify=args.function,
        file_containing_function_to_verify=args.file,
        path_to_call_graph=args.call_graph,
        replace_recursive_calls=args.replace_recursive_calls,
    )
    print(response)
    sys.exit(0 if returncode == 0 else 1)


if __name__ == "__main__":
    main()
