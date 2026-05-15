"""Run CBMC on a function.

Usage:
    % avocado-run-cbmc --function <FUNCTION_NAME> \
                       --file <PATH_TO_C_FILE> \
                       [--I <PATH_TO_INCLUDE_DIR(S)>]...
"""

import argparse
import json
import shlex
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from tools.construct_call_graph import construct_call_graph
from tools.util import (
    build_stub_index,
    get_in_file_callees_for,
    get_stub_paths_for,
    get_unstubbed_external_callees_for,
)
from tools.util.callgraph import CallGraph

# Char budget for failure responses, sized to keep CLI output bounded so it
# doesn't exceed an agent's tool-output limits.
_MAX_RESPONSE_CHARS = 100_000

# Of the output size budget left after the header, FAILURE lines, and section labels, this
# fraction is given to the stdout tail; the remainder goes to the stderr tail.
_STDOUT_TAIL_SHARE = 0.7

_DISABLE_MACRO_FLAGS = [
    "-D__NO_CTYPE"  # for ctype.h
]


def main() -> None:
    """Run CBMC on a function."""
    parser = argparse.ArgumentParser(
        description=(
            "Run CBMC on a function with loop unwinding = 5, depth = 100. "
            "Exits with status 0 on verification success."
        )
    )
    parser.add_argument("--function", required=True, help="Name of the function to verify.")
    parser.add_argument("--file", required=True, help="Path to the C file defining the function.")
    parser.add_argument(
        "-I",
        "--include-dir",
        action="append",
        default=[],
        dest="include_dirs",
        metavar="DIR",
        help="Directory to add to the include search path. May be repeated.",
    )
    args = parser.parse_args()
    response, returncode = run_cbmc(
        function_to_verify=args.function,
        file_containing_function_to_verify=args.file,
        include_dirs=args.include_dirs,
    )
    print(response)
    sys.exit(returncode)


def run_cbmc(
    function_to_verify: str,
    file_containing_function_to_verify: str,
    include_dirs: list[str] | None = None,
) -> tuple[str, int]:
    """Run CBMC on the given function with loop unwinding = 5, depth = 100.

    Args:
        function_to_verify (str): Name of the function to verify.
        file_containing_function_to_verify (str): Path to the C file defining the function.
        include_dirs (list[str] | None): Directories to add to the C preprocessor's include
            search path. Forwarded to `goto-cc` as `-I` flags.

    Returns:
        A (response_text, returncode) tuple. The text is a success message or a
            truncated failure block; the returncode is CBMC's exit code (0 on success).
    """
    path_to_raw_call_graph = construct_call_graph(file_containing_function_to_verify)
    call_graph = CallGraph(json.loads(Path(path_to_raw_call_graph).read_text(encoding="utf-8")))
    callees = get_in_file_callees_for(function_to_verify, call_graph)
    # Building the stub index is inexpensive for now (there is a single file).
    # Re-visit this if/when we have more stub files to parse.
    stub_index = build_stub_index()
    stub_paths = get_stub_paths_for(function_to_verify, call_graph, stub_index)
    nondet_callees = get_unstubbed_external_callees_for(function_to_verify, call_graph, stub_index)
    cbmc_command = get_cbmc_command(
        function_to_verify,
        callees,
        file_containing_function_to_verify,
        stub_paths=stub_paths,
        include_dirs=include_dirs,
    )
    # Try the simplest verification command.
    result = subprocess.run(cbmc_command, capture_output=True, text=True, shell=True, check=False)
    _log_invocation(
        file_containing_function_to_verify,
        function_to_verify,
        cbmc_command,
        result.returncode,
        nondet_callees,
    )
    # Check if the failure is related to recursion and retry, if appropriate.
    if result.returncode != 0 and has_recursion_inlining_error_message(
        function_to_verify, result.stdout, result.stderr
    ):
        callees = get_in_file_callees_for(
            function_to_verify,
            call_graph,
            include_self=call_graph.is_self_recursive(function_to_verify),
        )
        cbmc_command = get_cbmc_command(
            function_to_verify,
            callees,
            file_containing_function_to_verify,
            stub_paths=stub_paths,
            include_dirs=include_dirs,
        )
        result = subprocess.run(
            cbmc_command, capture_output=True, text=True, shell=True, check=False
        )
        _log_invocation(
            file_containing_function_to_verify,
            function_to_verify,
            cbmc_command,
            result.returncode,
            nondet_callees,
        )

    # Check if the failure is related to missing callee bodies and retry, if appropriate.
    if result.returncode != 0 and has_missing_body_for_callee_message(result.stdout, result.stderr):
        cbmc_command = get_cbmc_command(
            function_to_verify,
            callees,
            file_containing_function_to_verify,
            prevent_macro_expansion=True,
            stub_paths=stub_paths,
            include_dirs=include_dirs,
        )
        result = subprocess.run(
            cbmc_command, capture_output=True, text=True, shell=True, check=False
        )
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


def has_recursion_inlining_error_message(function: str, stdout: str, stderr: str) -> bool:
    """Return True iff CBMC reports an error with inlining given function, which might be recursive.

    Args:
        function (str): The function that might be recursive.
        stdout (str): The stdout of a CBMC command.
        stderr (str): The stderr of a CBMC command.

    Returns:
        bool: True iff CBMC reports an error with inlining given function, which might be recursive.
    """
    recursion_error_message = f"Recursive call to '{function}' during inlining"
    return recursion_error_message in stdout or recursion_error_message in stderr


def has_missing_body_for_callee_message(stdout: str, stderr: str) -> bool:
    """Return True iff CBMC output indicates a callee body is missing.

    The CBMC error output contains the string "no body for callee" when a callee of a function under
    verification is missing its body. In this case, it doesn't hurt to re-run CBMC while suppressing
    macro expansion (e.g., `isspace` in ctype.h expands to `__ctype_loc`, which CBMC lacks a model
    for).

    Args:
        stdout (str): The stdout of a CBMC command.
        stderr (str): The stderr of a CBMC command.

    Returns:
        bool: True iff CBMC output indicates a callee body is missing.
    """
    missing_callee_indicator = "no body for callee"
    return missing_callee_indicator in stdout or missing_callee_indicator in stderr


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


def get_cbmc_command(
    function_to_verify: str,
    callees: list[str],
    file_containing_function: str,
    prevent_macro_expansion: bool = False,
    stub_paths: list[str] | None = None,
    include_dirs: list[str] | None = None,
) -> str:
    """Return the command that should be used to verify a function in a C file with CBMC.

    The command will run CBMC with a loop unrolling bound of 5, and a symbolic exploration depth of
    100 along execution paths.

    The `prevent_macro_expansion` flag adds steps that helps code using standard headers
    verify cleanly:

    * `goto-cc` is passed `-D__NO_CTYPE` so glibc's `<ctype.h>` exposes plain function declarations
      instead of macros that expand to `(*__ctype_b_loc())[c] & _ISspace`. CBMC has no model for
      glibc's internal `__ctype_b_loc()`, so without this flag the call site references an
      unmodeled symbol and verification can't reason about `isspace`/`isalpha`/etc.
    * `goto-instrument --add-library` is run after `goto-cc` to inject CBMC's bundled C-library
      models (`isspace`, `strchr`, etc.) into the goto-binary before `--enforce-contract` runs.
      Without this, those calls are treated as nondeterministic, which loosens precision and
      can cause `goto-instrument` to emit "no body for function" warnings.

    Args:
        function_to_verify (str): The function to verify.
        callees (list[str]): The callees of the function to verify.
        file_containing_function (str): The path to the file containing the function to verify.
        prevent_macro_expansion (bool): True iff any macro expansions should be disabled.
            Defaults to False.
        stub_paths (list[str] | None): Extra `.c` stub files to compile in alongside the
            source file. Used to provide bodies for external callees that CBMC's bundled
            library does not model (e.g., POSIX terminal-control functions). Defaults to
            None.
        include_dirs (list[str] | None): Directories to add to the C preprocessor's include
            search path. Each is forwarded to `goto-cc` as a `-I` flag. Defaults to None.

    Returns:
        str: The CBMC command that should be used by Claude.
    """
    quoted_function_to_verify = shlex.quote(function_to_verify)
    replace_calls = "".join(f" --replace-call-with-contract {shlex.quote(c)}" for c in callees)
    flags_disabling_macro_expansion = (
        f"{' '.join(_DISABLE_MACRO_FLAGS)} " if prevent_macro_expansion else ""
    )
    extra_stub_args = f" {' '.join(shlex.quote(p) for p in stub_paths)}" if stub_paths else ""
    include_flags = "".join(f" -I {shlex.quote(d)}" for d in include_dirs) if include_dirs else ""
    inject_cbmc_model_command = (
        (
            f"goto-instrument --add-library "
            f"{quoted_function_to_verify}.goto {quoted_function_to_verify}.goto"
        )
        if prevent_macro_expansion
        else ""
    )
    commands = [
        (
            f"goto-cc {flags_disabling_macro_expansion}-o {quoted_function_to_verify}.goto"
            f"{include_flags} "
            f"{shlex.quote(file_containing_function)}{extra_stub_args} "
            f"--function {quoted_function_to_verify}"
        ),
        inject_cbmc_model_command,
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
    return " && ".join(c for c in commands if c)


if __name__ == "__main__":
    main()
