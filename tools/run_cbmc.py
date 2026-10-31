"""Run CBMC on a function.

Usage:
    % avocado-run-cbmc --function <FUNCTION_NAME> \
                       --file <PATH_TO_C_FILE> \
                       [-I <PATH_TO_INCLUDE_DIR(S)>]...
"""

import argparse
import fcntl
import json
import os
import shlex
import stat
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from functools import cache
from pathlib import Path

from loguru import logger

from tools.construct_call_graph import construct_call_graph
from tools.util import (
    build_stub_index,
    get_in_file_callees_for,
    get_stub_paths_for,
    get_unstubbed_external_callees_for,
    run_command,
)
from tools.util.callgraph import CallGraph

# Character budget for failure responses. The harness persists full tool output to a file but
# previews only the head inline, so we keep responses bounded (a pathological CBMC run can
# emit hundreds of MB) and lead with the FAILURE lines (see `_format_failure_response`) so
# the verdict survives that head-only preview rather than being buried in the stdout tail.
_MAX_RESPONSE_CHARS = 100_000

# Of the budget left after the header, the leading FAILURE lines, and section labels, this
# fraction is given to the stdout tail; the remainder goes to the stderr tail.
_STDOUT_TAIL_SHARE = 0.7

_DISABLE_MACRO_FLAGS = [
    "-D__NO_CTYPE"  # for ctype.h
]

# 10-minute timeout.
_DEFAULT_RUN_CBMC_TIMEOUT_SEC = 600

# goto-cc is fast; if it hasn't finished in this many seconds, the input is almost
# certainly pathological and should be treated as uncompilable.
_GOTO_CC_TIMEOUT_SEC = 30

# GNU `timeout(1)` convention: distinguishes a timeout from a CBMC verification failure
# both in the CLI exit code and in the JSONL invocation log.
_TIMEOUT_RETURNCODE = 124

# `--unwind` argument to CBMC.
_CBMC_UNWIND = 5

# `--depth` argument to CBMC.
_CBMC_DEPTH = 200

# Machine-wide cap on how many pipeline subprocesses (`goto-cc`, `goto-instrument`, `cbmc`) may run
# at once. Pipelines are spawned concurrently both within a process (mutants of one function run
# in a thread pool) and across processes (for example, several `avocado-run-cbmc` invocations at
# once), and without a shared bound the product of those fan-outs could oversubscribe the machine,
# turning slow-but-decidable CBMC runs into timeouts. See `max_concurrent_subprocesses` for how the
# cap is chosen and `run_shell_command` for how it is enforced.
_MAX_CONCURRENT_SUBPROCESSES_ENV_VAR = "AVOCADO_MAX_CONCURRENT_CBMC"
_DEFAULT_MAX_CONCURRENT_SUBPROCESSES = 32

# Directory of the slot files that enforce the cap. It is shared by every process of this user
# that has the same temporary directory, which is the whole container under Docker.
_SLOT_DIR = Path(tempfile.gettempdir()) / f"avocado-cbmc-slots-{os.getuid()}"

# How often a caller waiting for a subprocess slot checks whether one has become free.
_SLOT_POLL_SEC = 0.1

# For each cap, a semaphore with that many permits. A thread holds a permit while it waits for a
# subprocess slot and while it uses the slot, so at most the cap's number of threads of this
# process poll the slot files; other threads of this process block on the semaphore instead.
_IN_PROCESS_SLOTS: dict[int, threading.BoundedSemaphore] = {}
_IN_PROCESS_SLOTS_LOCK = threading.Lock()


@cache
def max_concurrent_subprocesses() -> int:
    """Return the cap on how many pipeline subprocesses may run at once.

    The cap is the number of CPUs available to this process, further limited by
    `AVOCADO_MAX_CONCURRENT_CBMC` if that environment variable is set to a non-empty value, and
    otherwise by `_DEFAULT_MAX_CONCURRENT_SUBPROCESSES`. The CPU count is not the only limit
    because each CBMC run can use a lot of memory, so on many-core hosts RAM rather than CPUs is the
    binding constraint; lower `AVOCADO_MAX_CONCURRENT_CBMC` if mutation runs exhaust RAM.

    Returns:
        int: The cap, which is at least 1.

    Raises:
        ValueError: If `AVOCADO_MAX_CONCURRENT_CBMC` is set to something other than a positive
            integer.
    """
    limit = _DEFAULT_MAX_CONCURRENT_SUBPROCESSES
    setting = os.environ.get(_MAX_CONCURRENT_SUBPROCESSES_ENV_VAR, "").strip()
    if setting:
        try:
            limit = int(setting)
        except ValueError:
            limit = 0
        if limit < 1:
            env_var = _MAX_CONCURRENT_SUBPROCESSES_ENV_VAR
            message = f"{env_var} must be a positive integer, not {setting!r}"
            raise ValueError(message)
    return min(os.process_cpu_count() or 1, limit)


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
        """True iff the full pipeline ran without any step failing or timing out.

        Note: This is *not* equivalent to a successful verification result. See
        `is_function_verified`.

        """
        return self.failed_step is None and not self.timed_out

    @property
    def is_function_verified(self) -> bool:
        """True iff the function for this verification run is successfully verified.

        A function is successfully verified if all components of the CBMC pipeline ran successfully
        and the return code is 0.
        """
        return self.cbmc_ran_successfully and self.returncode == 0

    def __str__(self) -> str:
        """Return the string representation of this result, used for logging.

        Returns:
            str: The string representation of this result, used for logging.
        """
        if self.timed_out:
            return "TIMED_OUT"
        if self.failed_step:
            return f"{self.failed_step.value.upper()}_FAILED"
        if self.is_function_verified:
            return "PASS"
        return "FAIL"


@dataclass(frozen=True)
class _SubprocessResult:
    """Outcome of running one subprocess step.

    Attributes:
        step (CbmcStep): The logical step in the CBMC verification pipeline this subprocess belongs
            to.
        command (str): The shell command that was run.
        returncode (int): The subprocess exit code, or `_TIMEOUT_RETURNCODE` on timeout.
        stdout (str): Captured stdout (empty on timeout).
        stderr (str): Captured stderr (empty on timeout).
        timed_out (bool): True iff the subprocess hit the per-step timeout.
        timeout_sec (int): The per-step timeout that applied, for the timeout diagnostic.
    """

    step: CbmcStep
    command: str
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    timeout_sec: int = _DEFAULT_RUN_CBMC_TIMEOUT_SEC

    @property
    def succeeded(self) -> bool:
        """True iff the subprocess exited zero and did not time out."""
        return self.returncode == 0 and not self.timed_out


def run_shell_command(
    command: str, timeout: float, cwd: str | None = None
) -> subprocess.CompletedProcess[str] | None:
    """Run a shell command with `run_command`, once a subprocess slot is free.

    Every pipeline subprocess (`goto-cc`, `goto-instrument`, `cbmc`) should be run through this
    function. Then the number of them running at once, across all threads and all processes that
    share `_SLOT_DIR`, never exceeds `max_concurrent_subprocesses()` (or, if those processes
    disagree about the cap, the largest of their caps). The timeout starts once the subprocess is
    launched; waiting for a slot does not count against it. On timeout, the command and every
    process it started are killed, so no `cbmc` keeps running without holding a subprocess slot.

    Args:
        command (str): The shell command to run.
        timeout (float): Seconds after which the command and every process it started are killed.
        cwd (str | None): Working directory for the subprocess. When None, the subprocess
            inherits the current working directory.

    Returns:
        subprocess.CompletedProcess[str] | None: The finished subprocess, with its captured stdout
            and stderr, or None if it hit the timeout.
    """
    with _subprocess_slot():
        return run_command(command, timeout, cwd=cwd)


@contextmanager
def _subprocess_slot() -> Generator[None]:
    """Hold one of the `max_concurrent_subprocesses()` subprocess slots for the `with` body.

    Each slot is an exclusive `flock` on a file in `_SLOT_DIR`. An `flock` belongs to an open file
    description, so it excludes other threads of this process as well as other processes, and the
    kernel releases it when its process dies, so a killed process never leaks a slot. The lock's
    file descriptor is not inherited by subprocesses. Blocks until a slot is free: a thread first
    waits for a permit from this process's semaphore for the cap, then polls the slot files every
    `_SLOT_POLL_SEC`.

    Yields:
        None: Control, while the slot is held.

    Raises:
        PermissionError: If `_SLOT_DIR` is not a directory that this user owns and that no other
            user can write, since another user could then hold the slots or plant symbolic links.
    """
    _SLOT_DIR.mkdir(mode=0o700, exist_ok=True)
    slot_dir_status = os.lstat(_SLOT_DIR)
    if (
        not stat.S_ISDIR(slot_dir_status.st_mode)
        or slot_dir_status.st_uid != os.getuid()
        or slot_dir_status.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
    ):
        message = f"{_SLOT_DIR} must be a directory that this user owns and no other user can write"
        raise PermissionError(message)
    cap = max_concurrent_subprocesses()
    with _IN_PROCESS_SLOTS_LOCK:
        in_process_slots = _IN_PROCESS_SLOTS.setdefault(cap, threading.BoundedSemaphore(cap))
    slot_paths = [_SLOT_DIR / f"slot-{i}" for i in range(cap)]
    with in_process_slots:
        while True:
            for slot_path in slot_paths:
                fd = os.open(slot_path, os.O_RDWR | os.O_CREAT, 0o600)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    os.close(fd)
                    continue
                try:
                    yield
                finally:
                    os.close(fd)
                return
            time.sleep(_SLOT_POLL_SEC)


def main() -> None:
    """Run CBMC on a function."""
    parser = argparse.ArgumentParser(
        description=(
            "Run CBMC on a function with loop unwinding = _CBMC_UNWIND, depth = _CBMC_DEPTH. "
            "Exits with status 0 on verification success. "
            "On success, additionally runs mutation testing, which re-runs the full CBMC "
            "pipeline once per mutant (sequentially, up to a 10-minute timeout each); this can "
            "take several minutes, during which the tool is mostly silent. This is expected, "
            "not a hang -- do not interrupt the process. Consider running it in the background "
            "and polling for completion."
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

    # Tee mutation-testing compile failures into a file. Leaves loguru's default stderr sink
    # untouched; the filter keys on the warning emitted by `_verify_mutant` in
    # tools/util/mutation.py.
    logger.add(
        "mutation_compile_failures.log",
        level="WARNING",
        filter=lambda record: "failed to compile" in record["message"],
    )

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
    call_graph: CallGraph | None = None,
    timeout_sec: int = _DEFAULT_RUN_CBMC_TIMEOUT_SEC,
) -> RunCbmcResult:
    """Run CBMC on the given function with loop unwinding = `_UNWIND`, depth = `_DEPTH`.

    The pipeline is split into three logical steps — `goto-cc`, `goto-instrument`, and
    `cbmc` — each run as its own subprocess so that failures can be attributed to a
    specific step. Up to three pipeline attempts are made: an initial run, a retry
    when an apparent recursive-inlining error is detected, and a retry when a missing
    callee body is detected. The timeout is per CBMC subprocess attempt, not a total
    budget; a timeout in any single step terminates the whole call.

    The pipeline's subprocesses run in a fresh private temporary directory, which receives the
    intermediate `<function>.goto` / `checking-<function>-contracts.goto` files and is removed
    afterward. Therefore, concurrent calls (even for the same function, as in mutation testing)
    never clobber one another's intermediates, and no files are left in the caller's working
    directory. Relative paths in the arguments are interpreted relative to the caller's working
    directory, as usual.

    Args:
        function_to_verify (str): Name of the function to verify.
        file_containing_function_to_verify (str): Path to the C file defining the function.
        include_dirs (list[str] | None): Directories to add to the C preprocessor's include
            search path. Forwarded to `goto-cc` as `-I` flags.
        call_graph (CallGraph | None): When provided, this pre-built call graph is used instead
            of parsing `file_containing_function_to_verify`. Callers that verify many variants of
            one source (e.g. mutation testing, where every mutant shares the original's call
            graph) can build it once and pass it in, which both avoids redundant parsing and keeps
            the non-thread-safe tree-sitter parser out of concurrent code paths. When None, the
            call graph is constructed from the source file as usual.
        timeout_sec (int): Per-subprocess timeout in seconds. Defaults to
            `_DEFAULT_RUN_CBMC_TIMEOUT_SEC`; callers that only need a quick verdict (e.g. mutation
            feedback for the agent) can pass a smaller budget. A timed-out run is reported as such
            and is never mistaken for a verification result.

    Returns:
        RunCbmcResult: The outcome of the run, naming the failed step (if any) and carrying
            a printable response and the relevant exit code.
    """
    with tempfile.TemporaryDirectory(prefix="avocado-cbmc-") as scratch_dir:
        return _run_cbmc_in_directory(
            function_to_verify,
            file_containing_function_to_verify,
            include_dirs,
            call_graph,
            scratch_dir,
            timeout_sec,
        )


def _run_cbmc_in_directory(
    function_to_verify: str,
    file_containing_function_to_verify: str,
    include_dirs: list[str] | None,
    call_graph: CallGraph | None,
    cwd: str,
    timeout_sec: int,
) -> RunCbmcResult:
    """Run CBMC on the given function as `run_cbmc` does, but run every subprocess in `cwd`.

    Args:
        function_to_verify (str): Name of the function to verify.
        file_containing_function_to_verify (str): Path to the C file defining the function.
        include_dirs (list[str] | None): Directories forwarded to `goto-cc` as `-I` flags.
        call_graph (CallGraph | None): A pre-built call graph, or None to construct one.
        cwd (str): Working directory for every subprocess in the pipeline.
        timeout_sec (int): Per-subprocess timeout in seconds.

    Returns:
        RunCbmcResult: The outcome of the run.
    """
    # The pipeline's commands are executed relative to `cwd`, so relative paths given by the
    # caller (which are relative to the process's working directory) must be resolved first. The
    # invocation log still records the path as the caller gave it.
    resolved_file = str(Path(file_containing_function_to_verify).resolve())
    include_dirs = [str(Path(d).resolve()) for d in include_dirs or []]

    if call_graph is None:
        path_to_raw_call_graph = construct_call_graph(file_containing_function_to_verify)
        call_graph = CallGraph(json.loads(Path(path_to_raw_call_graph).read_text(encoding="utf-8")))
    callees = get_in_file_callees_for(function_to_verify, call_graph)
    # Building the stub index is inexpensive for now (there is a single file).
    # Re-visit this if/when we have more stub files to parse.
    stub_index = build_stub_index()
    stub_paths = get_stub_paths_for(function_to_verify, call_graph, stub_index)
    nondet_callees = get_unstubbed_external_callees_for(function_to_verify, call_graph, stub_index)

    subprocess_results: list[dict] = []

    # Initial attempt.
    result, combined_stdout, combined_stderr = _run_pipeline(
        function_to_verify,
        callees,
        resolved_file,
        stub_paths=stub_paths,
        include_dirs=include_dirs,
        prevent_macro_expansion=False,
        subprocess_results=subprocess_results,
        cwd=cwd,
        timeout_sec=timeout_sec,
    )
    # First, check if the run was successful or if it timed out.
    if result.cbmc_ran_successfully or result.timed_out:
        _log_invocation(
            file_containing_function_to_verify,
            result,
            subprocess_results,
            nondet_callees,
        )
        return result

    # Recursion-inlining retry.
    if has_recursion_inlining_error_message(function_to_verify, combined_stdout, combined_stderr):
        callees = get_in_file_callees_for(
            function_to_verify,
            call_graph,
            include_self=call_graph.is_self_recursive(function_to_verify),
        )
        result, combined_stdout, combined_stderr = _run_pipeline(
            function_to_verify,
            callees,
            resolved_file,
            stub_paths=stub_paths,
            include_dirs=include_dirs,
            prevent_macro_expansion=False,
            subprocess_results=subprocess_results,
            cwd=cwd,
            timeout_sec=timeout_sec,
        )

    # Missing-body retry if unsuccessful: re-run with macro expansion suppressed.
    if (
        not result.cbmc_ran_successfully
        and not result.timed_out
        and has_missing_body_for_callee_or_function_message(combined_stdout, combined_stderr)
    ):
        result, combined_stdout, combined_stderr = _run_pipeline(
            function_to_verify,
            callees,
            resolved_file,
            stub_paths=stub_paths,
            include_dirs=include_dirs,
            prevent_macro_expansion=True,
            subprocess_results=subprocess_results,
            cwd=cwd,
            timeout_sec=timeout_sec,
        )

    _log_invocation(file_containing_function_to_verify, result, subprocess_results, nondet_callees)
    return result


def _run_pipeline(
    function_to_verify: str,
    callees: list[str],
    file_containing_function: str,
    stub_paths: list[str] | None,
    include_dirs: list[str] | None,
    prevent_macro_expansion: bool,
    subprocess_results: list[dict],
    cwd: str | None = None,
    timeout_sec: int = _DEFAULT_RUN_CBMC_TIMEOUT_SEC,
) -> tuple[RunCbmcResult, str, str]:
    """Run the goto-cc → goto-instrument → cbmc pipeline once.

    Each subprocess is run separately so the first failure (or timeout) can be attributed
    to its logical step. Subprocess results are appended to `subprocess_results` for the JSONL log.

    Args:
        function_to_verify (str): The function under verification.
        callees (list[str]): Callees of the function, used by `--replace-call-with-contract`.
        file_containing_function (str): The C source file containing the function.
        stub_paths (list[str] | None): Extra `.c` stub files compiled in alongside the source.
        include_dirs (list[str] | None): Directories forwarded to `goto-cc` as `-I` flags.
        prevent_macro_expansion (bool): When True, disable macros CBMC can't model and inject
            the bundled C-library models before contract enforcement.
        subprocess_results (list[dict]): Mutated in place by callee (_run_command). One dict per
            subprocess invocation is appended in-order. Used by `_log_invocation` to produce the
            JSONL row.
        cwd (str | None): Working directory for every subprocess, forwarded to `_run_step`. The
            pipeline's intermediate `.goto` files are written relative to this directory.
        timeout_sec (int): Per-subprocess timeout in seconds, forwarded to `_run_command`.

    Returns:
        tuple[RunCbmcResult, str, str]: The result of the pipeline plus the concatenated
            stdout and stderr across every subprocess that ran. The concatenated output is used by
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

    per_step_stdout = []
    per_step_stderr = []
    for step, command in commands:
        subprocess_result = _run_command(
            step, command, subprocess_results, cwd=cwd, timeout_sec=timeout_sec
        )
        per_step_stdout.append(subprocess_result.stdout)
        per_step_stderr.append(subprocess_result.stderr)
        if not subprocess_result.succeeded:
            combined_stdout = "".join(per_step_stdout)
            combined_stderr = "".join(per_step_stderr)
            return (
                _result_from_failure(
                    function_to_verify,
                    subprocess_result,
                    combined_stdout,
                    combined_stderr,
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
        "".join(per_step_stdout),
        "".join(per_step_stderr),
    )


def _run_command(
    step: CbmcStep,
    command: str,
    subprocess_results,
    cwd: str | None = None,
    timeout_sec: int = _DEFAULT_RUN_CBMC_TIMEOUT_SEC,
) -> _SubprocessResult:
    """Run one pipeline step as a subprocess with the per-step timeout.

    Args:
        step (CbmcStep): The logical step this subprocess belongs to.
        command (str): The shell command to run.
        subprocess_results: List of subprocess results; is side-effected.
        cwd (str | None): Working directory for the subprocess. When None, the subprocess
            inherits the current working directory. Distinct directories let concurrent
            pipelines for the same function avoid clobbering each other's `.goto` files.
        timeout_sec (int): Seconds the subprocess may run before it is killed and reported as
            timed out.

    Returns:
        _SubprocessResult: The captured outcome, including stdout/stderr or the timeout sentinel.
    """
    completed = run_shell_command(command, timeout_sec, cwd=cwd)
    if completed is None:
        returncode = _TIMEOUT_RETURNCODE
        stdout = ""
        stderr = ""
        timed_out = True
    else:
        returncode = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
        timed_out = False

    subprocess_results.append(
        {
            "step": step.value,
            "command": command,
            "returncode": returncode,
        }
    )
    return _SubprocessResult(
        step=step,
        command=command,
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
        timeout_sec=timeout_sec,
    )


def _result_from_failure(
    function: str,
    subprocess_result: _SubprocessResult,
    combined_stdout: str,
    combined_stderr: str,
) -> RunCbmcResult:
    """Build a `RunCbmcResult` for a pipeline that failed at `subprocess_result`.

    Args:
        function (str): The function under verification.
        subprocess_result (_SubprocessResult): The subprocess that failed or timed out.
        combined_stdout (str): Concatenated stdout across every subprocess that ran (including
            the failing one). Used to render the failure response.
        combined_stderr (str): Concatenated stderr across every subprocess that ran.

    Returns:
        RunCbmcResult: A failure result whose `response` is a formatted, truncated block.
    """
    if subprocess_result.timed_out:
        response = (
            f"Verification for '{function}' timed out after "
            f"{subprocess_result.timeout_sec} second(s) during {subprocess_result.step.value}"
        )
        return RunCbmcResult(
            function=function,
            failed_step=subprocess_result.step,
            timed_out=True,
            returncode=_TIMEOUT_RETURNCODE,
            response=response,
        )
    response = _format_failure_response(
        function, subprocess_result.step, combined_stdout, combined_stderr
    )
    return RunCbmcResult(
        function=function,
        failed_step=subprocess_result.step,
        timed_out=False,
        returncode=subprocess_result.returncode,
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


def has_missing_body_for_callee_or_function_message(stdout: str, stderr: str) -> bool:
    """Return True iff CBMC output indicates a callee or function body is missing.

    The CBMC error output contains the string "no body for callee" when a callee of a function under
    verification is missing its body.

    It contains the string "no body for function" in cases where a function body may be missing
    (e.g., when a stub file is not passed in).

    In this case, it doesn't hurt to re-run CBMC while suppressing
    macro expansion (e.g., `isspace` in ctype.h expands to `__ctype_loc`, which CBMC lacks a model
    for).

    Args:
        stdout (str): The stdout of a CBMC command.
        stderr (str): The stderr of a CBMC command.

    Returns:
        bool: True iff CBMC output indicates a callee body is missing.
    """
    missing_callee_indicator = "no body for callee"
    missing_function_indicator = "no body for function"
    return (
        missing_callee_indicator in stdout
        or missing_callee_indicator in stderr
        or missing_function_indicator in stdout
        or missing_function_indicator in stderr
    )


def _format_failure_response(function: str, failed_step: CbmcStep, stdout: str, stderr: str) -> str:
    """Format a CBMC failure response, leading with the FAILURE lines, possibly truncated.

    After the header, the response always begins with the FAILURE lines extracted from
    stdout, so the verdict survives the harness's head-only inline preview: CBMC prints its
    FAILURE lines and verdict at the *tail* of stdout, which a positional truncation would
    otherwise bury. The full stderr and stdout follow; when the combined output exceeds
    `_MAX_RESPONSE_CHARS`, each stream is replaced by its tail behind a truncation marker.
    For `goto-cc`/`goto-instrument` failures stdout has no FAILURE lines, so the response
    leads with stderr, where those tools print their diagnostics.

    Args:
        function (str): The name of the function that failed verification.
        failed_step (CbmcStep): The pipeline step that failed.
        stdout (str): The concatenated stdout across every subprocess that ran.
        stderr (str): The concatenated stderr across every subprocess that ran.

    Returns:
        str: The formatted CBMC failure response, leading with the FAILURE lines and
            truncated iff it has exceeded the char budget.
    """
    if failed_step is CbmcStep.CBMC:
        header = f"{function} failed to verify with the following errors:\n\n"
    else:
        header = f"{function} failed at {failed_step.value} with the following errors:\n\n"

    failure_section = _failure_lines_section(stdout)

    # Lead with the FAILURE lines, then show both streams in full when everything fits.
    full = f"{header}{failure_section}--- stderr ---\n{stderr}\n--- stdout ---\n{stdout}"
    if len(full) <= _MAX_RESPONSE_CHARS:
        return full

    # Over budget: keep the leading FAILURE lines and replace each stream with its tail.
    # The `_MAX_RESPONSE_CHARS`-wide placeholder pads the marker's digit count so the
    # actual marker (with the real dropped count) cannot push us over budget.
    digit_pad = str(_MAX_RESPONSE_CHARS)
    marker = f"[... {digit_pad} characters truncated ...]\n"
    fixed = (
        f"{header}{failure_section}--- stderr (tail) ---\n{marker}--- stdout (tail) ---\n{marker}"
    )
    remaining = max(_MAX_RESPONSE_CHARS - len(fixed), 0)
    stdout_budget = int(remaining * _STDOUT_TAIL_SHARE)
    stderr_budget = remaining - stdout_budget

    stderr_section = _tail_section("stderr (tail)", stderr, stderr_budget)
    stdout_section = _tail_section("stdout (tail)", stdout, stdout_budget)

    response = f"{header}{failure_section}{stderr_section}{stdout_section}"
    # Hard clamp: the per-section budget accounting can drift by a few chars
    # against the `fixed` estimate, so guarantee we never exceed the cap.
    return response[:_MAX_RESPONSE_CHARS]


def _failure_lines_section(stdout: str) -> str:
    """Render the leading "FAILURE lines" section of a failure response, or "" if none.

    Extracts the FAILURE lines from `stdout` and renders them as a labeled section, capped
    at half the total budget so a pathological run with tens of thousands of FAILURE lines
    can't blow past the limit on its own. Returns the empty string when stdout has no
    FAILURE lines (e.g. a goto-cc compile failure), so callers naturally lead with stderr.

    Args:
        stdout (str): The concatenated stdout across every step that ran.

    Returns:
        str: The labeled FAILURE-lines section ending in a newline, or "" if there are none.
    """
    failure_lines = [line for line in stdout.split("\n") if "FAILURE" in line]
    if not failure_lines:
        return ""
    failure_block = "\n".join(failure_lines)
    failure_cap = _MAX_RESPONSE_CHARS // 2
    if len(failure_block) > failure_cap:
        dropped = len(failure_block) - failure_cap
        failure_block = (
            f"[... {dropped} characters of FAILURE lines truncated ...]\n"
            f"{failure_block[-failure_cap:]}"
        )
    return f"--- FAILURE lines ---\n{failure_block}\n"


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
    subprocess_results: list[dict],
    nondet_callees: list[str],
) -> None:
    """Log a CBMC invocation with the given arguments.

    Args:
        file_under_verification (str): The file that contains the function under verification.
        result (RunCbmcResult): The final outcome of the `run_cbmc` call.
        subprocess_results (list[dict]): Per-subprocess records (one entry per subprocess invocation
            across every pipeline attempt), each with keys `step`, `command`, `returncode`.
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
        "subprocess_results": subprocess_results,
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
        f"{' '.join(_DISABLE_MACRO_FLAGS)}" if prevent_macro_expansion else ""
    )
    extra_stub_args = " ".join(shlex.quote(p) for p in stub_paths) if stub_paths else ""
    include_flags = " ".join(f"-I {shlex.quote(d)}" for d in include_dirs) if include_dirs else ""
    return " ".join(
        (
            f"goto-cc {flags_disabling_macro_expansion}",
            f"-o {quoted_function}.goto",
            include_flags,
            shlex.quote(file_containing_function),
            extra_stub_args,
            f"--function {quoted_function}",
        )
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
    """Return the `goto-instrument --partial-loops --unwind _CBMC_UNWIND` command for `function`.

    Args:
        function (str): The function whose goto-binary should be unwound.

    Returns:
        str: A single shell command.
    """
    quoted_function = shlex.quote(function)
    return (
        f"goto-instrument --partial-loops --unwind {_CBMC_UNWIND} "
        f"{quoted_function}.goto {quoted_function}.goto"
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
        f"cbmc checking-{quoted_function}-contracts.goto "
        f"--function {quoted_function} --depth {_CBMC_DEPTH}"
    )


def compile_with_goto_cc(
    function: str,
    file_path: str,
    include_dirs: list[str] | None = None,
) -> int:
    """Run only the goto-cc compile command on a C file and return its exit code.

    A zero exit code means goto-cc accepted the input; non-zero means it rejected it.
    Mutants that do not compile should be excluded from evaluation and calculations;
    this is the client's responsibility.

    Args:
        function (str): The function used as the goto-binary entry point.
        file_path (str): Path to the C file to compile.
        include_dirs (list[str] | None): Directories forwarded as `-I` flags.

    Returns:
        int: the return code of the subprocess used to invoke `goto-cc`,
            (0 == compiled successfully).
    """
    compilation_command = _get_goto_cc_command(function, file_path, include_dirs=include_dirs)
    completed = run_shell_command(compilation_command, _GOTO_CC_TIMEOUT_SEC)
    if completed is None:
        return _TIMEOUT_RETURNCODE
    return completed.returncode


if __name__ == "__main__":
    main()
