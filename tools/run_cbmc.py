"""Run CBMC on a function.

Usage:
    % avocado-run-cbmc --function <FUNCTION_NAME> \
                       --file <PATH_TO_C_FILE> \
                       [-I <PATH_TO_INCLUDE_DIR(S)>]...
"""

import argparse
import json
import shlex
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from subprocess import TimeoutExpired

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

# 5-minute timeout.
_DEFAULT_RUN_CBMC_TIMEOUT_SEC = 300

# goto-cc is fast; if it hasn't finished in this many seconds, the input is almost
# certainly pathological and should be treated as uncompilable.
_GOTO_CC_TIMEOUT_SEC = 30

# GNU `timeout(1)` convention: distinguishes a timeout from a CBMC verification failure
# both in the CLI exit code and in the JSONL invocation log.
_TIMEOUT_RETURNCODE = 124


class CbmcStep(StrEnum):
    """Logical step in the CBMC verification pipeline.

    `run_cbmc` runs the pipeline as three logical steps; each maps to one or more
    underlying subprocess invocations. When a step fails, the result names the step
    so callers can distinguish e.g. a goto-cc compile rejection from a cbmc
    verification failure.
    """

    GOTO_CC = "goto-cc"
    GOTO_INSTRUMENT = "goto-instrument"
    CBMC = "cbmc"


@dataclass(frozen=True)
class RunCbmcResult:
    """The outcome of running CBMC on a function.

    Attributes:
        function (str): The function under verification.
        failed_step (CbmcStep | None): The pipeline step that failed, or None on success.
        timed_out (bool): True iff any step hit the per-attempt timeout.
        returncode (int): 0 on success, `_TIMEOUT_RETURNCODE` on timeout, otherwise the
            exit code of the failing subprocess (cbmc's verification exit code when
            `failed_step` is CBMC).
        response (str): Printable summary — a success line or a truncated failure block.
    """

    function: str
    failed_step: CbmcStep | None
    timed_out: bool
    returncode: int
    response: str

    @property
    def cbmc_ran_successfully(self) -> bool:
        """Return True iff the full pipeline ran without any step failing or timing out.

        Note: This is *not* equivalent to a successful verification result. See
        `is_function_verified`.

        """
        return self.failed_step is None and not self.timed_out

    @property
    def is_function_verified(self) -> bool:
        """Return True iff the function for this verification run is successfully verified.

        A function is successfully verified if all components of the CBMC pipeline ran successfully
        and the return code is 0.

        Returns:
            bool: True iff the function for this verification result was verified.
        """
        return self.cbmc_ran_successfully and self.returncode == 0

    def __str__(self) -> str:
        """Return the string representation of this result, used for logging.

        Returns:
            str: The string representation of this result, used for logging.
        """
        if failed_step := self.failed_step:
            return f"{failed_step.value.upper()}_FAILED"
        if self.timed_out:
            return "TIMED_OUT"
        return "PASS" if self.is_function_verified else "FAIL"


@dataclass(frozen=True)
class _StepRun:
    """Outcome of running one subprocess step. Internal; collapsed into RunCbmcResult.

    Attributes:
        step (CbmcStep): The logical step this subprocess belongs to.
        command (str): The shell command that was run.
        returncode (int): The subprocess exit code, or `_TIMEOUT_RETURNCODE` on timeout.
        stdout (str): Captured stdout (empty on timeout).
        stderr (str): Captured stderr (empty on timeout).
        timed_out (bool): True iff the subprocess hit the per-step timeout.
    """

    step: CbmcStep
    command: str
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool

    @property
    def succeeded(self) -> bool:
        """Return True iff the subprocess exited zero and did not time out."""
        return self.returncode == 0 and not self.timed_out


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
    result = run_cbmc(
        function_to_verify=args.function,
        file_containing_function_to_verify=args.file,
        include_dirs=args.include_dirs,
    )
    print(result.response)
    sys.exit(result.returncode)


def run_cbmc(
    function_to_verify: str,
    file_containing_function_to_verify: str,
    include_dirs: list[str] | None = None,
) -> RunCbmcResult:
    """Run CBMC on the given function with loop unwinding = 5, depth = 100.

    The pipeline is split into three logical steps — `goto-cc`, `goto-instrument`, and
    `cbmc` — each run as its own subprocess so that failures can be attributed to a
    specific step. Up to three pipeline attempts are made: an initial run, a retry
    when an apparent recursive-inlining error is detected, and a retry when a missing
    callee body is detected. The timeout is per CBMC subprocess attempt, not a total
    budget; a timeout in any single step terminates the whole call.

    Args:
        function_to_verify (str): Name of the function to verify.
        file_containing_function_to_verify (str): Path to the C file defining the function.
        include_dirs (list[str] | None): Directories to add to the C preprocessor's include
            search path. Forwarded to `goto-cc` as `-I` flags.

    Returns:
        RunCbmcResult: The outcome of the run, naming the failed step (if any) and carrying
            a printable response and the relevant exit code.
    """
    path_to_raw_call_graph = construct_call_graph(file_containing_function_to_verify)
    call_graph = CallGraph(json.loads(Path(path_to_raw_call_graph).read_text(encoding="utf-8")))
    callees = get_in_file_callees_for(function_to_verify, call_graph)
    # Building the stub index is inexpensive for now (there is a single file).
    # Re-visit this if/when we have more stub files to parse.
    stub_index = build_stub_index()
    stub_paths = get_stub_paths_for(function_to_verify, call_graph, stub_index)
    nondet_callees = get_unstubbed_external_callees_for(function_to_verify, call_graph, stub_index)

    step_records: list[dict] = []

    # Initial attempt.
    result, combined_stdout, combined_stderr = _run_pipeline(
        function_to_verify,
        callees,
        file_containing_function_to_verify,
        stub_paths=stub_paths,
        include_dirs=include_dirs,
        prevent_macro_expansion=False,
        step_records=step_records,
    )
    if result.timed_out:
        _log_invocation(file_containing_function_to_verify, result, step_records, nondet_callees)
        return result

    # Recursion-inlining retry.
    if not result.cbmc_ran_successfully and has_recursion_inlining_error_message(
        function_to_verify, combined_stdout, combined_stderr
    ):
        callees = get_in_file_callees_for(
            function_to_verify,
            call_graph,
            include_self=call_graph.is_self_recursive(function_to_verify),
        )
        result, combined_stdout, combined_stderr = _run_pipeline(
            function_to_verify,
            callees,
            file_containing_function_to_verify,
            stub_paths=stub_paths,
            include_dirs=include_dirs,
            prevent_macro_expansion=False,
            step_records=step_records,
        )
        if result.timed_out:
            _log_invocation(
                file_containing_function_to_verify, result, step_records, nondet_callees
            )
            return result

    # Missing-body retry: re-run with macro expansion suppressed.
    if not result.cbmc_ran_successfully and has_missing_body_for_callee_message(
        combined_stdout, combined_stderr
    ):
        result, combined_stdout, combined_stderr = _run_pipeline(
            function_to_verify,
            callees,
            file_containing_function_to_verify,
            stub_paths=stub_paths,
            include_dirs=include_dirs,
            prevent_macro_expansion=True,
            step_records=step_records,
        )
        if result.timed_out:
            _log_invocation(
                file_containing_function_to_verify, result, step_records, nondet_callees
            )
            return result

    _log_invocation(file_containing_function_to_verify, result, step_records, nondet_callees)
    return result


def _run_pipeline(
    function_to_verify: str,
    callees: list[str],
    file_containing_function: str,
    stub_paths: list[str] | None,
    include_dirs: list[str] | None,
    prevent_macro_expansion: bool,
    step_records: list[dict],
) -> tuple[RunCbmcResult, str, str]:
    """Run the goto-cc → goto-instrument → cbmc pipeline once.

    Each subprocess is run separately so the first failure (or timeout) can be attributed
    to its logical step. Step records are appended to `step_records` for the JSONL log.

    Args:
        function_to_verify (str): The function under verification.
        callees (list[str]): Callees of the function, used by `--replace-call-with-contract`.
        file_containing_function (str): The C source file containing the function.
        stub_paths (list[str] | None): Extra `.c` stub files compiled in alongside the source.
        include_dirs (list[str] | None): Directories forwarded to `goto-cc` as `-I` flags.
        prevent_macro_expansion (bool): When True, disable macros CBMC can't model and inject
            the bundled C-library models before contract enforcement.
        step_records (list[dict]): Mutated in place — one record per subprocess invocation
            appended in order. Used by `_log_invocation` to produce the JSONL row.

    Returns:
        tuple[RunCbmcResult, str, str]: The result of the pipeline plus the concatenated
            stdout and stderr across every step that ran. The concatenated output is used by
            the retry triggers in `run_cbmc`.
    """
    commands: list[tuple[CbmcStep, str]] = [
        (
            CbmcStep.GOTO_CC,
            _get_goto_cc_command(
                function_to_verify,
                file_containing_function,
                stub_paths=stub_paths,
                include_dirs=include_dirs,
                prevent_macro_expansion=prevent_macro_expansion,
            ),
        ),
    ]
    if prevent_macro_expansion:
        commands.append(
            (
                CbmcStep.GOTO_INSTRUMENT,
                _get_goto_instrument_add_library_command(function_to_verify),
            )
        )
    commands.extend(
        [
            (
                CbmcStep.GOTO_INSTRUMENT,
                _get_goto_instrument_unwind_command(function_to_verify),
            ),
            (
                CbmcStep.GOTO_INSTRUMENT,
                _get_goto_instrument_contract_command(function_to_verify, callees),
            ),
            (CbmcStep.CBMC, _get_cbmc_check_command(function_to_verify)),
        ]
    )

    combined_stdout = ""
    combined_stderr = ""
    for step, command in commands:
        step_run = _run_step(step, command)
        step_records.append(
            {
                "step": step.value,
                "command": step_run.command,
                "returncode": step_run.returncode,
            }
        )
        combined_stdout += step_run.stdout
        combined_stderr += step_run.stderr
        if not step_run.succeeded:
            return (
                _result_from_failure(
                    function_to_verify, step_run, combined_stdout, combined_stderr
                ),
                combined_stdout,
                combined_stderr,
            )

    return (
        RunCbmcResult(
            function=function_to_verify,
            failed_step=None,
            timed_out=False,
            returncode=0,
            response=f"{function_to_verify} verified successfully",
        ),
        combined_stdout,
        combined_stderr,
    )


def _run_step(step: CbmcStep, command: str) -> _StepRun:
    """Run one pipeline step as a subprocess with the per-step timeout.

    Args:
        step (CbmcStep): The logical step this subprocess belongs to.
        command (str): The shell command to run.

    Returns:
        _StepRun: The captured outcome, including stdout/stderr or the timeout sentinel.
    """
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            shell=True,
            check=False,
            timeout=_DEFAULT_RUN_CBMC_TIMEOUT_SEC,
        )
    except TimeoutExpired:
        return _StepRun(
            step=step,
            command=command,
            returncode=_TIMEOUT_RETURNCODE,
            stdout="",
            stderr="",
            timed_out=True,
        )
    return _StepRun(
        step=step,
        command=command,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        timed_out=False,
    )


def _result_from_failure(
    function: str, step_run: _StepRun, combined_stdout: str, combined_stderr: str
) -> RunCbmcResult:
    """Build a `RunCbmcResult` for a pipeline that failed at `step_run`.

    Args:
        function (str): The function under verification.
        step_run (_StepRun): The step that failed or timed out.
        combined_stdout (str): Concatenated stdout across every step that ran (including
            the failing one). Used to render the failure response.
        combined_stderr (str): Concatenated stderr across every step that ran.

    Returns:
        RunCbmcResult: A failure result whose `response` is a formatted, truncated block.
    """
    if step_run.timed_out:
        response = (
            f"Verification for '{function}' timed out after "
            f"{_DEFAULT_RUN_CBMC_TIMEOUT_SEC} second(s) during {step_run.step.value}"
        )
        return RunCbmcResult(
            function=function,
            failed_step=step_run.step,
            timed_out=True,
            returncode=_TIMEOUT_RETURNCODE,
            response=response,
        )
    response = _format_failure_response(function, step_run.step, combined_stdout, combined_stderr)
    return RunCbmcResult(
        function=function,
        failed_step=step_run.step,
        timed_out=False,
        returncode=step_run.returncode,
        response=response,
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


def _format_failure_response(function: str, failed_step: CbmcStep, stdout: str, stderr: str) -> str:
    """Format a CBMC failure response, truncating only if it exceeds the char budget.

    When the combined labeled output fits within `_MAX_RESPONSE_CHARS`, both streams are
    returned in full. Otherwise, FAILURE lines from stdout are preserved and the rest of
    each stream is replaced by its tail, with an explicit truncation marker.

    Args:
        function (str): The name of the function that failed verification.
        failed_step (CbmcStep): The pipeline step that failed.
        stdout (str): The concatenated stdout across every step that ran.
        stderr (str): The concatenated stderr across every step that ran.

    Returns:
        str: The formatted CBMC failure response, truncated iff it has exceeded the char budget.
    """
    if failed_step is CbmcStep.CBMC:
        header = f"{function} failed to verify with the following errors:\n\n"
    else:
        header = f"{function} failed at {failed_step.value} with the following errors:\n\n"
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
    result: RunCbmcResult,
    step_records: list[dict],
    nondet_callees: list[str],
) -> None:
    """Log a CBMC invocation with the given arguments.

    Args:
        file_under_verification (str): The file that contains the function under verification.
        result (RunCbmcResult): The final outcome of the `run_cbmc` call.
        step_records (list[dict]): Per-subprocess records (one entry per step invocation across
            every pipeline attempt), each with keys `step`, `command`, `returncode`.
        nondet_callees (list[str]): The list of callees that CBMC treated as non-deterministic
            during verification.
    """
    source_path = Path(file_under_verification)
    log_path = source_path.with_name(f"{source_path.stem}-cbmc-runs.jsonl")
    record = {
        "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "function": result.function,
        "file": file_under_verification,
        "failed_step": result.failed_step.value if result.failed_step is not None else None,
        "returncode": result.returncode,
        "timed_out": result.timed_out,
        "steps": step_records,
        "nondet_callees": nondet_callees,
    }
    try:
        with log_path.open("a") as f:
            f.write(json.dumps(record) + "\n")
    except OSError:
        # This does fail silently, but it shouldn't stop the tool from making progress.
        pass


def _get_goto_cc_command(
    function: str,
    file_containing_function: str,
    stub_paths: list[str] | None = None,
    include_dirs: list[str] | None = None,
    prevent_macro_expansion: bool = False,
) -> str:
    """Return the goto-cc command that compiles `file_containing_function` to a goto-binary.

    This is the first step of the CBMC pipeline, exposed as a standalone helper so
    callers can run only the compile phase — useful for cheaply detecting inputs that goto-cc
    rejects (e.g., a body-mutated source where mutating `p - q` to `p + q` is illegal C).

    Args:
        function (str): The function used as the goto-binary entry point.
        file_containing_function (str): Path to the C source file.
        stub_paths (list[str] | None): Extra `.c` stub files compiled in alongside the source.
        include_dirs (list[str] | None): Directories forwarded as `-I` flags.
        prevent_macro_expansion (bool): If True, disable macros that CBMC cannot model.

    Returns:
        str: A single shell command suitable for `subprocess.run(..., shell=True)`.
    """
    quoted_function = shlex.quote(function)
    flags_disabling_macro_expansion = (
        f"{' '.join(_DISABLE_MACRO_FLAGS)} " if prevent_macro_expansion else ""
    )
    extra_stub_args = f" {' '.join(shlex.quote(p) for p in stub_paths)}" if stub_paths else ""
    include_flags = "".join(f" -I {shlex.quote(d)}" for d in include_dirs) if include_dirs else ""
    return (
        f"goto-cc {flags_disabling_macro_expansion}-o {quoted_function}.goto"
        f"{include_flags} "
        f"{shlex.quote(file_containing_function)}{extra_stub_args} "
        f"--function {quoted_function}"
    )


def _get_goto_instrument_add_library_command(function: str) -> str:
    """Return the `goto-instrument --add-library` command for `function`'s goto-binary.

    This step injects CBMC's bundled C-library models (`isspace`, `strchr`, etc.) into
    the goto-binary before contract enforcement runs. It's only used when macro expansion
    is suppressed; without it, those calls are treated as nondeterministic, which loosens
    precision and can cause `goto-instrument` to emit "no body for function" warnings.

    Args:
        function (str): The function whose goto-binary should receive the library models.

    Returns:
        str: A single shell command.
    """
    quoted_function = shlex.quote(function)
    return f"goto-instrument --add-library {quoted_function}.goto {quoted_function}.goto"


def _get_goto_instrument_unwind_command(function: str) -> str:
    """Return the `goto-instrument --partial-loops --unwind 5` command for `function`.

    Args:
        function (str): The function whose goto-binary should be unwound.

    Returns:
        str: A single shell command.
    """
    quoted_function = shlex.quote(function)
    return (
        f"goto-instrument --partial-loops --unwind 5 {quoted_function}.goto {quoted_function}.goto"
    )


def _get_goto_instrument_contract_command(function: str, callees: list[str]) -> str:
    """Return the `goto-instrument --enforce-contract` command for `function`.

    Args:
        function (str): The function whose contract should be enforced.
        callees (list[str]): Callees whose contracts should be substituted for the call sites
            via `--replace-call-with-contract`.

    Returns:
        str: A single shell command.
    """
    quoted_function = shlex.quote(function)
    replace_calls = "".join(f" --replace-call-with-contract {shlex.quote(c)}" for c in callees)
    return (
        f"goto-instrument{replace_calls} "
        f"--enforce-contract {quoted_function} "
        f"{quoted_function}.goto "
        f"checking-{quoted_function}-contracts.goto"
    )


def _get_cbmc_check_command(function: str) -> str:
    """Return the final `cbmc` command that checks `function`'s instrumented goto-binary.

    Args:
        function (str): The function under verification.

    Returns:
        str: A single shell command.
    """
    quoted_function = shlex.quote(function)
    return (
        f"cbmc checking-{quoted_function}-contracts.goto --function {quoted_function} --depth 100"
    )


def compile_with_goto_cc(
    function: str,
    file_path: str,
    include_dirs: list[str] | None = None,
) -> int:
    """Run only the goto-cc compile step on a C file and return its exit code.

    A zero exit code means goto-cc accepted the input; non-zero means it rejected it.
    Mutants that do not compile should be excluded from evaluation and calculations;
    this is the client's responsibility.

    Args:
        function (str): The function used as the goto-binary entry point.
        file_path (str): Path to the C file to compile.
        include_dirs (list[str] | None): Directories forwarded as `-I` flags.

    Returns:
        int: goto-cc's exit code (0 == compiled successfully).
    """
    compilation_command = _get_goto_cc_command(function, file_path, include_dirs=include_dirs)
    try:
        result = subprocess.run(
            compilation_command,
            capture_output=True,
            text=True,
            shell=True,
            check=False,
            timeout=_GOTO_CC_TIMEOUT_SEC,
        )
    except TimeoutExpired:
        return _TIMEOUT_RETURNCODE
    return result.returncode


if __name__ == "__main__":
    main()
