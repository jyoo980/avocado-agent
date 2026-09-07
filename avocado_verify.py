#!/usr/bin/env python3

"""Generate and verify a program with Avocado.

Calls Claude Code over the functions of a C file in callee-first topological order.

Verifying callees before their callers means subsequent sessions have access to previously-generated
specifications (i.e., session to verify callers have access to any callee specs).

For each function the harness runs a fresh `claude -p` session prompting Claude to generate a CBMC
specification for a function. Once Claude reports it is finished, this harness independently runs
CBMC to record a ground-truth verification result.

Every session works in a private copy of the source file's directory (see `_fork_session`), and
when it ends the harness splices the function's definition -- and any new top-level helpers the
agent added for its contract -- back into the canonical file (`tools.util.contract_merge`). That
isolation is what allows `--jobs N` to run several functions' sessions at once: a function starts
as soon as the callees it depends on are merged, so a file's wall-clock is bounded by its longest
dependency chain rather than by the sum over its functions.

Usage:
    % avocado-verify --file <PATH_TO_C_FILE> \
        [--claude-timeout <TIMEOUT>] \
        [--jobs <N>] \
        [--keep-sessions] \
        [--resume-from <PATH_TO_JSONL_LOG>]
"""

import argparse
import json
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from subprocess import TimeoutExpired

from loguru import logger

from eval.mutants.mutate_function import get_mutants
from tools.construct_call_graph import construct_call_graph
from tools.get_topological_ordering_of_functions import get_topological_ordering_of_functions
from tools.run_cbmc import RunCbmcResult, compile_with_goto_cc
from tools.run_cbmc_and_mutation_testing import VERIFICATION_ATTEMPTS_LOG_SUFFIX, verify_function
from tools.util import get_in_file_callees_for, get_in_file_callers_of
from tools.util.callgraph import CallGraph
from tools.util.contract_merge import MergeReport, merge_function

# Per-function wall-clock budget for a single `claude -p` session. A session may run CBMC
# several times (each with its own multi-minute timeout) across the coverage and quality
# passes, so this default is deliberately generous.
_DEFAULT_CLAUDE_TIMEOUT_SEC = 1800

# GNU `timeout(1)` convention, matching `tools.run_cbmc`: distinguishes a timeout from an
# ordinary non-zero exit in both the recorded return code and the run log.
_TIMEOUT_RETURNCODE = 124

# 0 indicates all functions have been verified, 1 indicates some functions may not be verified.
_EXIT_ALL_VERIFIED = 0
_EXIT_SOME_UNVERIFIED = 1
# 2 indicates an early stop due to a usage limit being hit.
_EXIT_USAGE_LIMITED = 2

# `main` is never specified (see CLAUDE.md); skip it wherever it appears in the ordering.
_UNVERIFIABLE_FUNCTIONS = frozenset({"main"})

# Cap on the raw stdout/stderr snippet kept when Claude's JSON output cannot be parsed.
_MAX_PARSE_SNIPPET_CHARS = 500

# Case-insensitive substrings in claude's `result` text that indicate the session was stopped by a
# usage/rate limit rather than a genuine task failure. How a usage limit surfaces in
# `claude -p --output-format json` is not formally documented, so detection is a text match kept in
# one place; adjust these as the CLI's wording evolves.
_USAGE_LIMIT_RESULT_PATTERNS = ("usage limit reached", "rate limit", "resets ")

# The loop will not advance to the next function until the agent has *attempted* verification
# (run `avocado-run-cbmc`) at least this many times for the current function, as counted from the
# verification-attempts log. This guards against advancing on a session that barely tried.
_MIN_VERIFICATION_ATTEMPTS_PER_SESSION = 2

# Upper bound on how many `claude -p` sessions a single function may receive while trying to reach
# `_MIN_VERIFICATION_ATTEMPTS`. Prevents an unproductive session from looping forever; once hit,
# the harness proceeds anyway and records the shortfall.
_MAX_AGENT_SESSIONS_PER_FUNCTION = 3

# How many functions' sessions run at once unless `--jobs` says otherwise. Sequential by default:
# concurrency spends the account's usage limit several times faster per wall-clock hour, and the
# numbers recorded in WORK_SO_FAR.md were taken sequentially.
_DEFAULT_JOBS = 1

# Prefixes of the temporary directories that hold a session's private copy of the source directory
# and the consistent copy the ground-truth verification runs on.
_SESSION_DIR_PREFIX = "avocado-session-"
_GROUND_TRUTH_DIR_PREFIX = "avocado-ground-truth-"

# What is left out when the source directory is copied for a session or a ground-truth run, and
# what `_report_stray_edits` does not count as an edit: the harness's and the tool's own logs and
# caches (including the compile-failure log the tool writes into whatever directory the agent runs
# it from), CBMC intermediates, and mutant sources. Each copy then starts clean and, in particular,
# gets its own `<stem>-verification-attempts.jsonl`.
_SNAPSHOT_IGNORE = shutil.ignore_patterns(
    ".git",
    "*.goto",
    "*-callgraph.json",
    "*.jsonl",
    "*__mutant_*.c",
    "*__clause_drop_*.c",
    "mutation_compile_failures.log",
)

# Serialises every read-for-merge and write of the canonical file, and every directory copy taken
# of it, so a copy is always internally consistent and merges never interleave.
_CANONICAL_LOCK = threading.Lock()

# Serialises appends to the run log and the canonical-side attempts log from worker threads.
_RUN_LOG_LOCK = threading.Lock()

# A session that ends without a single verification attempt on the file is re-run at most this many
# times in a row. One such session is bad luck; a second, identical one is evidence that the prompt
# leads this agent nowhere on this function, and a third would repeat it. In the measured runs the
# one function that reached this point burned two full 1800 s sessions without an attempt before
# a third succeeded, and that third session had copied another run's finished contract.
_MAX_SESSIONS_WITHOUT_ATTEMPT = 1


class GroundTruthVerificationResult(StrEnum):
    """Ground truth verification result, corresponding to an invocation of `tools.run_cbmc.py`.

    Corresponds to this harness's own CBMC re-run, not Claude's self-report.
    """

    VERIFIED = "VERIFIED"
    UNVERIFIED = "UNVERIFIED"
    CLAUDE_TIMED_OUT = "CLAUDE_TIMED_OUT"
    CLAUDE_ERROR = "CLAUDE_ERROR"
    USAGE_LIMITED = "USAGE_LIMITED"


# Outcomes that count as "processed" for `--resume-from`, so a resumed run skips them.
# USAGE_LIMITED is deliberately excluded: that session never got a fair attempt, so it is retried.
_PROCESSED_FUNCTION_OUTCOMES = frozenset(
    {
        GroundTruthVerificationResult.VERIFIED,
        GroundTruthVerificationResult.UNVERIFIED,
        GroundTruthVerificationResult.CLAUDE_ERROR,
        GroundTruthVerificationResult.CLAUDE_TIMED_OUT,
    }
)


@dataclass(frozen=True)
class ClaudeRun:
    """The outcome of a single `claude -p` session.

    Note on the confusingly-named `subtype` field: the JSON output of a `claude -p` session contains
    a `subtype` key that maps to a string. This is used for logging some finer-grained information
    about the end-result of a session.

    Attributes:
        returncode (int): claude's exit code, or `_TIMEOUT_RETURNCODE` on timeout.
        timed_out (bool): True iff the session hit the per-function timeout.
        is_error (bool): True iff claude reported an error result, or its output could
            not be parsed as JSON.
        session_id (str | None): claude's session id, when reported.
        result_text (str): claude's final message (or a diagnostic on failure).
        total_cost_usd (float | None): Per-session cost in USD, when reported.
        num_turns (int | None): number of turns taken, when reported.
        duration_ms (int | None): wall-clock duration claude reported, when present.
        subtype (str | None): The subtype of claude's terminal result message from
            `claude -p --output-format json`: "success" on normal completion, or an error
            subtype such as "error_max_turns" or "error_during_execution" otherwise. A
            finer-grained companion to `is_error`. Recorded in the run log for diagnostics
            only; not consulted by any control flow (usage-limit detection matches on
            `result_text`, see `_is_usage_limit_hit`).
    """

    returncode: int
    timed_out: bool
    is_error: bool
    session_id: str | None
    result_text: str
    total_cost_usd: float | None
    num_turns: int | None
    duration_ms: int | None
    subtype: str | None


@dataclass(frozen=True)
class FunctionVerificationResult:
    """The combined outcome for one function: a Claude session plus a CBMC re-run.

    Attributes:
        function (str): The function under verification.
        outcome (GroundTruthVerificationResult): The harness's overall verdict for the function.
        claude_sessions (list[ClaudeRun]): The `claude -p` sessions that resulted in the spec.
        cbmc (RunCbmcResult): The independent CBMC verification the harness ran afterward.
        internal_callees (list[str]): The function's in-file callees, for the run log.
        verification_attempts (int): How many times the agent attempted verification (ran
            `avocado-run-cbmc`) for this function across all its sessions.
        agent_sessions (int): How many `claude -p` sessions this function received before the
            harness moved on (>= 1; > 1 when re-runs were needed to reach the attempt floor).
        merge (MergeReport | None): What the merge of the session's copy into the canonical file
            kept and dropped, or None when no merge was attempted.
        stray_files (list[str]): Files other than the source file that the agent added or changed
            in its private copy; they are never merged, only reported.
    """

    function: str
    outcome: GroundTruthVerificationResult
    claude_sessions: list[ClaudeRun]
    cbmc: RunCbmcResult
    internal_callees: list[str]
    verification_attempts: int
    agent_sessions: int
    merge: MergeReport | None = None
    stray_files: list[str] = field(default_factory=list)

    def to_record(self) -> dict:
        """Return a JSON-serializable record of this result for the run log.

        Returns:
            dict: A timestamped record capturing the Claude session and CBMC verdict.
        """
        claude_session_records = [asdict(session) for session in self.claude_sessions]
        total_cost_to_verify_usd: float = sum(
            session.total_cost_usd or 0 for session in self.claude_sessions
        )
        return {
            "timestamp": datetime.now(UTC).isoformat(),
            "function": self.function,
            "outcome": str(self.outcome),
            "internal_callees": self.internal_callees,
            "verification_attempts": self.verification_attempts,
            "agent_sessions": self.agent_sessions,
            "claude": claude_session_records,
            "cbmc": {
                "verdict": str(self.cbmc),
                "is_function_verified": self.cbmc.is_function_verified,
                "returncode": self.cbmc.returncode,
                "timed_out": self.cbmc.timed_out,
                "failed_step": None
                if self.cbmc.failed_step is None
                else self.cbmc.failed_step.value,
            },
            "total_cost_to_verify_usd": total_cost_to_verify_usd,
            "merge": None
            if self.merge is None
            else {**self.merge.to_record(), "stray_files": list(self.stray_files)},
        }


def main() -> None:
    """Generate and verify CBMC specifications for functions in a C program."""
    parser = argparse.ArgumentParser(
        description=(
            "Generate and verify CBMC specifications for a C file with Claude Code, one "
            "function at a time in callee-first order. Each function gets a fresh "
            "`claude -p` session; the harness then re-runs CBMC to record an objective verdict."
        )
    )
    parser.add_argument("--file", required=True, help="Path to the C file to verify.")
    parser.add_argument(
        "--claude-timeout",
        type=int,
        default=_DEFAULT_CLAUDE_TIMEOUT_SEC,
        metavar="SECONDS",
        help=(
            "Per-function timeout for a `claude -p` session in seconds "
            f"(default: {_DEFAULT_CLAUDE_TIMEOUT_SEC})."
        ),
    )
    parser.add_argument(
        "--resume-from",
        required=False,
        type=str,
        help=(
            "Resume from the given avocado-verify.jsonl: skip already-completed functions and "
            "append to the log instead of truncating it. Use after a run stopped due to a usage "
            "limit (exit code 2)."
        ),
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=_DEFAULT_JOBS,
        metavar="N",
        help=(
            "How many functions to specify concurrently; each function starts as soon as the "
            f"callees it depends on are done (default: {_DEFAULT_JOBS}, sequential)."
        ),
    )
    parser.add_argument(
        "--keep-sessions",
        action="store_true",
        help="Keep each session's private copy of the source directory for inspection.",
    )
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be at least 1.")

    file_path = Path(args.file).resolve()
    if not file_path.is_file():
        logger.error(f"No such file: {file_path}")
        sys.exit(1)

    if include_dirs := _autodetect_include_dirs(str(file_path)):
        logger.info(f"[auto-include] using {include_dirs}")

    path_to_call_graph = construct_call_graph(str(file_path))
    call_graph = CallGraph(json.loads(Path(path_to_call_graph).read_text(encoding="utf-8")))
    functions = _get_functions_to_verify(str(file_path))
    if not functions:
        logger.warning(f"No verifiable functions found in {file_path}")
        sys.exit(0)

    if args.resume_from:
        log_path = Path(args.resume_from).resolve()
        if not log_path.is_file():
            logger.error(f"No resume log at: {log_path}")
            sys.exit(1)
        already_done = _get_processed_functions(log_path)
    else:
        log_path = file_path.with_name(f"{file_path.stem}-avocado-verify.jsonl")
        already_done = set()
        log_path.write_text("", encoding="utf-8")  # Fresh run: truncate any prior log.

    num_functions = len(functions)
    pending = [function for function in functions if function not in already_done]
    if args.resume_from and already_done:
        logger.info(
            f"Resuming; {len(already_done)}/{num_functions} function(s) already processed, "
            f"{len(pending)} remaining"
        )

    if not pending:
        logger.info(f"Nothing to do; all {num_functions} function(s) already complete.")
        _finalize_run(
            log_path, functions=functions, remaining=[], status="completed", stopped_early=False
        )

    logger.info(
        f"{len(pending)} function(s) to verify in callees-first order: {', '.join(pending)}"
    )

    results, usage_limited = _verify_functions(
        pending,
        order=functions,
        file_path=str(file_path),
        call_graph=call_graph,
        timeout=args.claude_timeout,
        include_dirs=include_dirs,
        jobs=args.jobs,
        log_path=log_path,
        keep_sessions=args.keep_sessions,
    )
    if usage_limited:
        completed = {
            result.function
            for result in results
            if result.outcome is not GroundTruthVerificationResult.USAGE_LIMITED
        }
        remaining = [function for function in pending if function not in completed]
        logger.warning(
            f"stopped after {len(completed)}/{len(pending)} function(s) this run due to a usage "
            f"limit; {len(remaining)} remaining. Resume with --resume-from."
        )
        _log_summary(results, log_path)
        _finalize_run(
            log_path,
            functions=functions,
            remaining=remaining,
            status="usage_limited",
            stopped_early=True,
        )

    _log_summary(results, log_path)
    _finalize_run(
        log_path, functions=functions, remaining=[], status="completed", stopped_early=False
    )


def _get_functions_to_verify(file_path: str) -> list[str]:
    path_to_call_graph = construct_call_graph(str(file_path))
    return [
        function
        for function in get_topological_ordering_of_functions(path_to_call_graph)
        if function not in _UNVERIFIABLE_FUNCTIONS
    ]


def _finalize_run(
    log_path: Path,
    *,
    functions: list[str],
    remaining: list[str],
    status: str,
    stopped_early: bool,
) -> None:
    """Append the terminal run-summary record and exit with the appropriate code.

    Verification is accounted for across all runs by re-reading the log, so a resumed run reflects
    functions verified by earlier invocations rather than only this invocation's results.

    Args:
        log_path (Path): Path to the run log.
        functions (list[str]): All verifiable functions in the file, in topological order.
        remaining (list[str]): Functions still pending (empty on normal completion).
        status (str): "usage_limited" or "completed" for the summary record.
        stopped_early (bool): True iff the run stopped on a usage limit; selects the exit code.
    """
    verified_set = _verified_functions(log_path)
    verified = sum(1 for function in functions if function in verified_set)
    done = [function for function in functions if function not in remaining]
    _append_jsonl(
        log_path,
        _run_summary_record(
            status, done=done, remaining=remaining, verified=verified, total=len(functions)
        ),
    )
    if stopped_early:
        sys.exit(_EXIT_USAGE_LIMITED)
    sys.exit(_EXIT_ALL_VERIFIED if verified == len(functions) else _EXIT_SOME_UNVERIFIED)


@dataclass(frozen=True)
class _Session:
    """A private copy of the source directory in which one function's sessions run.

    Attributes:
        function (str): The function the session works on.
        directory (Path): The temporary root holding the copy; removed after the merge unless the
            run keeps sessions.
        file (Path): The copy of the source file inside `directory`.
        fork_base (bytes): The canonical file's bytes when the copy was taken; the merge compares
            the finished copy against it to see what the agent changed.
    """

    function: str
    directory: Path
    file: Path
    fork_base: bytes


def _fork_session(function: str, canonical_file: Path) -> _Session:
    """Copy the canonical file's directory into a fresh temporary directory for one session.

    The copy is taken under `_CANONICAL_LOCK` so it is consistent with `fork_base`: no merge can
    land between reading the file and copying the directory. `_SNAPSHOT_IGNORE` leaves out logs,
    caches, CBMC intermediates and mutant sources, so the copy starts clean and the tool's
    verification-attempts log for this session starts empty beside the copy.

    Args:
        function (str): The function the session will work on.
        canonical_file (Path): Absolute path to the canonical source file.

    Returns:
        _Session: The private copy.
    """
    with _CANONICAL_LOCK:
        fork_base = canonical_file.read_bytes()
        root = Path(tempfile.mkdtemp(prefix=_SESSION_DIR_PREFIX))
        directory = root / canonical_file.parent.name
        shutil.copytree(canonical_file.parent, directory, ignore=_SNAPSHOT_IGNORE)
    return _Session(
        function=function,
        directory=root,
        file=directory / canonical_file.name,
        fork_base=fork_base,
    )


def _verify_via_agent(
    function: str,
    *,
    file_path: str,
    call_graph: CallGraph,
    timeout: int,
    include_dirs: list[str],
    keep_sessions: bool = False,
) -> FunctionVerificationResult:
    """Specify `function` in a private copy, merge the result back, and re-verify it with CBMC.

    Runs one or more `claude -p` sessions against a fresh copy of the source directory
    (`_fork_session`, `_run_sessions_for`), then splices the function's finished definition and
    any new helpers into the canonical file and runs the independent ground truth on a consistent
    copy of the result (`_merge_and_verify`). Safe to call for several functions concurrently as
    long as their callees are already merged.

    Args:
        function (str): The function to specify and verify.
        file_path (str): Absolute path to the canonical C file defining the function.
        call_graph (CallGraph): Call graph of the file, used to record in-file callees.
        timeout (int): Per-function timeout for a `claude -p` session, in seconds.
        include_dirs (list[str]): Extra include directories to expose to the agent and forward to
            CBMC's include search path.
        keep_sessions (bool): When True, the session's copy is kept for inspection.

    Returns:
        FunctionVerificationResult: The combined Claude/CBMC outcome for the function.
    """
    canonical_file = Path(file_path)
    session = _fork_session(function, canonical_file)
    try:
        sessions, attempts, session_count = _run_sessions_for(
            function,
            session_file=session.file,
            call_graph=call_graph,
            timeout=timeout,
            include_dirs=include_dirs,
        )
        stray_files = _report_stray_edits(session, canonical_file.parent)
        _mirror_attempts_log(session, canonical_file)
        report, cbmc = _merge_and_verify(session, canonical_file, include_dirs=include_dirs)
    finally:
        if keep_sessions:
            logger.info(f"{function}: session copy kept at {session.directory}")
        else:
            shutil.rmtree(session.directory, ignore_errors=True)
    return FunctionVerificationResult(
        function=function,
        outcome=_outcome_for(sessions[-1], cbmc),
        claude_sessions=sessions,
        cbmc=cbmc,
        internal_callees=call_graph.get_callees(function).internal,
        verification_attempts=attempts,
        agent_sessions=session_count,
        merge=report,
        stray_files=stray_files,
    )


def _run_sessions_for(
    function: str,
    *,
    session_file: Path,
    call_graph: CallGraph,
    timeout: int,
    include_dirs: list[str],
) -> tuple[list[ClaudeRun], int, int]:
    """Run the `claude -p` session(s) for `function` against its private copy of the file.

    Re-runs the session while `_should_rerun_session` says another one can do better; every
    re-run carries a retry note saying why. Verification attempts are counted from the attempts
    log beside `session_file`, which the tool writes for exactly this copy.

    Args:
        function (str): The function to specify.
        session_file (Path): The session's private copy of the source file.
        call_graph (CallGraph): Call graph of the file, for the prompt's callee/caller lists.
        timeout (int): Per-session timeout in seconds.
        include_dirs (list[str]): Include directories forwarded to the agent and the tool.

    Returns:
        tuple[list[ClaudeRun], int, int]: The sessions run, the verification attempts they made,
            and the number of sessions.
    """
    file_path = str(session_file)
    attempts_log_path = session_file.with_name(
        f"{session_file.stem}{VERIFICATION_ATTEMPTS_LOG_SUFFIX}"
    )

    # Attempts already logged for this function before this turn (e.g. by an earlier function's
    # session that also exercised this one); gate only on attempts made from here forward.
    previous_verification_attempts = _count_verification_attempts(attempts_log_path, function)

    def attempts_so_far() -> int:
        return (
            _count_verification_attempts(attempts_log_path, function)
            - previous_verification_attempts
        )

    # Do not advance to the next function until the agent has attempted verification at least
    # `_MIN_VERIFICATION_ATTEMPTS` times, re-running the session up to a capped number of times --
    # but only while a re-run can plausibly do better than the session before it (see
    # `_should_rerun_session`). A re-run is never identical: its prompt says why it is happening.
    command = _build_claude_command(
        _build_prompt(
            function, file_path=file_path, call_graph=call_graph, include_dirs=include_dirs
        ),
        file_path=file_path,
        include_dirs=include_dirs,
    )
    claude_sessions_for_function = [_run_claude(command, timeout)]
    attempts_after_each_session = [attempts_so_far()]
    while True:
        decision = _should_rerun_session(
            claude_sessions_for_function,
            attempts_after_each_session,
            improvable=is_spec_improvable_with_mutation_testing(
                function, file_path, attempts_log_path
            ),
        )
        if not decision.rerun:
            break
        sessions = len(claude_sessions_for_function)
        logger.warning(
            f"{function}: re-running session ({sessions + 1}/{_MAX_AGENT_SESSIONS_PER_FUNCTION}): "
            f"{decision.rationale}"
        )
        retry_command = _build_claude_command(
            _build_prompt(
                function,
                file_path=file_path,
                call_graph=call_graph,
                include_dirs=include_dirs,
                retry_note=decision.rationale,
            ),
            file_path=file_path,
            include_dirs=include_dirs,
        )
        claude_sessions_for_function.append(_run_claude(retry_command, timeout))
        attempts_after_each_session.append(attempts_so_far())
    sessions = len(claude_sessions_for_function)
    current_verification_attempts = attempts_after_each_session[-1]
    if not decision.rerun and decision.rationale:
        logger.info(f"{function}: not re-running: {decision.rationale}")
    if not is_spec_improvable_with_mutation_testing(function, file_path, attempts_log_path):
        # Stopped deliberately, not short of the floor: there are no mutants to kill, so further
        # sessions cannot strengthen the (already verifying) spec.
        logger.info(
            f"{function}: verified with no mutants after {sessions} session(s); skipping the "
            f"attempt-count floor (no kill score to raise)"
        )
    elif current_verification_attempts < _MIN_VERIFICATION_ATTEMPTS_PER_SESSION:
        logger.warning(
            f"{function}: proceeding after {sessions} session(s) with only "
            f"{current_verification_attempts}/{_MIN_VERIFICATION_ATTEMPTS_PER_SESSION} "
            f"verification attempt(s)"
        )
    return claude_sessions_for_function, current_verification_attempts, sessions


def _merge_and_verify(
    session: _Session, canonical_file: Path, *, include_dirs: list[str]
) -> tuple[MergeReport, RunCbmcResult]:
    """Merge the session's copy into the canonical file, then run the independent ground truth.

    Under `_CANONICAL_LOCK`: read the canonical file, compute the merge
    (`tools.util.contract_merge.merge_function`), copy the canonical directory to a scratch
    directory, write the merged bytes into that copy and check that `goto-cc` still accepts the
    file. Only then is the canonical file replaced (atomically, via `os.replace`); a merge that
    does not compile is rejected and the canonical file left untouched. The lock is released
    before CBMC runs, on the scratch copy, so other sessions can merge meanwhile.

    Args:
        session (_Session): The finished session.
        canonical_file (Path): Absolute path to the canonical source file.
        include_dirs (list[str]): Include directories forwarded to `goto-cc` and CBMC.

    Returns:
        tuple[MergeReport, RunCbmcResult]: What the merge did, and the ground-truth verdict.
    """
    function = session.function
    absolute_include_dirs = [str(Path(directory).resolve()) for directory in include_dirs]
    with _CANONICAL_LOCK:
        canonical = canonical_file.read_bytes()
        try:
            snapshot = session.file.read_bytes()
        except OSError as error:
            merged, report = canonical, MergeReport(False, f"session copy unreadable: {error}")
        else:
            merged, report = merge_function(
                canonical=canonical,
                snapshot=snapshot,
                fork_base=session.fork_base,
                function=function,
            )
        scratch_root = Path(tempfile.mkdtemp(prefix=_GROUND_TRUTH_DIR_PREFIX))
        scratch_dir = scratch_root / canonical_file.parent.name
        shutil.copytree(canonical_file.parent, scratch_dir, ignore=_SNAPSHOT_IGNORE)
        scratch_file = scratch_dir / canonical_file.name
        if report.merged:
            scratch_file.write_bytes(merged)
            returncode = compile_with_goto_cc(
                function,
                str(scratch_file),
                include_dirs=absolute_include_dirs,
                cwd=str(scratch_dir),
            )
            if returncode != 0:
                report = replace(
                    report,
                    merged=False,
                    reason=f"merged file does not compile with goto-cc (exit code {returncode})",
                )
                scratch_file.write_bytes(canonical)
            else:
                staged = canonical_file.with_name(f"{canonical_file.name}.avocado-merge")
                staged.write_bytes(merged)
                Path(staged).replace(canonical_file)
    if report.merged:
        if report.transplanted:
            logger.info(f"{function}: transplanted {', '.join(report.transplanted)}")
        if report.dropped:
            logger.warning(
                f"{function}: dropped edits outside the function: {', '.join(report.dropped)}"
            )
    else:
        logger.warning(f"{function}: merge rejected: {report.reason}")
    try:
        cbmc = verify_function(function, str(scratch_file), include_dirs=include_dirs)
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)
    return report, cbmc


def _report_stray_edits(session: _Session, canonical_dir: Path) -> list[str]:
    """Return the files other than the source file that the agent added or changed in its copy.

    Headers, other sources and new files the agent created in the private copy are never merged;
    they are named here so the run log shows when a contract depended on one. A file is compared
    to the canonical directory's copy of it, which no session ever writes.

    Args:
        session (_Session): The finished session.
        canonical_dir (Path): The canonical source directory.

    Returns:
        list[str]: Relative paths tagged `added:` or `modified:`, in sorted order.
    """
    stray: list[str] = []
    session_dir = session.file.parent
    for path in sorted(session_dir.rglob("*")):
        if not path.is_file() or path == session.file:
            continue
        relative = path.relative_to(session_dir)
        if _SNAPSHOT_IGNORE(str(path.parent), [path.name]):
            continue
        original = canonical_dir / relative
        if not original.is_file():
            stray.append(f"added:{relative}")
        elif original.read_bytes() != path.read_bytes():
            stray.append(f"modified:{relative}")
    if stray:
        logger.warning(
            f"{session.function}: files changed in the session copy are not merged: {stray}"
        )
    return stray


def _mirror_attempts_log(session: _Session, canonical_file: Path) -> None:
    """Append the session copy's verification-attempt records to the canonical-side log.

    The harness itself reads attempts from the session copy; the canonical-side log is kept for
    post-hoc analysis of a run, where readers expect one log beside the source file. Failures to
    read or write are swallowed so logging never affects a run.

    Args:
        session (_Session): The finished session.
        canonical_file (Path): Absolute path to the canonical source file.
    """
    source_log = session.file.with_name(f"{session.file.stem}{VERIFICATION_ATTEMPTS_LOG_SUFFIX}")
    target_log = canonical_file.with_name(
        f"{canonical_file.stem}{VERIFICATION_ATTEMPTS_LOG_SUFFIX}"
    )
    try:
        records = source_log.read_text(encoding="utf-8")
    except OSError:
        return
    if not records.strip():
        return
    with _RUN_LOG_LOCK:
        try:
            with target_log.open("a", encoding="utf-8") as log_file:
                log_file.write(records if records.endswith("\n") else records + "\n")
        except OSError:
            pass


def _ready_functions(
    order: list[str], call_graph: CallGraph, *, done: set[str], active: set[str]
) -> list[str]:
    """Return the functions that may start now, in topological order.

    A function is ready when it is neither done nor running and every in-file callee of it that
    precedes it in `order` is done. Restricting to *earlier* callees reproduces the sequential
    harness exactly (each function waited only for functions before it), ignores self-recursion,
    and cannot deadlock on mutual recursion, whose members wait only for earlier members.

    Args:
        order (list[str]): All verifiable functions, callees first, as returned by
            `get_topological_ordering_of_functions`.
        call_graph (CallGraph): Call graph of the file.
        done (set[str]): Functions whose sessions have finished, or that an earlier run processed.
        active (set[str]): Functions whose sessions are running.

    Returns:
        list[str]: The ready functions, earliest in `order` first.
    """
    rank = {function: index for index, function in enumerate(order)}
    ready: list[str] = []
    for function in order:
        if function in done or function in active:
            continue
        callees = call_graph.get_callees(function).internal
        if all(
            callee in done for callee in callees if callee in rank and rank[callee] < rank[function]
        ):
            ready.append(function)
    return ready


def _verify_functions(
    pending: list[str],
    *,
    order: list[str],
    file_path: str,
    call_graph: CallGraph,
    timeout: int,
    include_dirs: list[str],
    jobs: int,
    log_path: Path,
    keep_sessions: bool,
) -> tuple[list[FunctionVerificationResult], set[str]]:
    """Specify every pending function, running up to `jobs` functions' sessions at once.

    Functions start as `_ready_functions` allows; each completion is merged, ground-truthed and
    appended to the run log before its dependents can start. The first session stopped by a usage
    limit halts new submissions; sessions already running finish and merge.

    Args:
        pending (list[str]): Functions still to process, in topological order.
        order (list[str]): All verifiable functions in the file, in topological order.
        file_path (str): Absolute path to the canonical C file.
        call_graph (CallGraph): Call graph of the file.
        timeout (int): Per-session timeout in seconds.
        include_dirs (list[str]): Include directories forwarded to the agent and CBMC.
        jobs (int): Maximum number of concurrent sessions.
        log_path (Path): The run log to append each function's record to.
        keep_sessions (bool): When True, session copies are kept for inspection.

    Returns:
        tuple[list[FunctionVerificationResult], set[str]]: The results in completion order, and
            the functions whose sessions were stopped by a usage limit.
    """
    done: set[str] = {function for function in order if function not in pending}
    usage_limited: set[str] = set()
    active: dict[Future[FunctionVerificationResult], str] = {}
    results: list[FunctionVerificationResult] = []
    started = 0
    pool = ThreadPoolExecutor(max_workers=jobs, thread_name_prefix="avocado-session")
    try:
        while True:
            if not usage_limited:
                for function in _ready_functions(
                    order, call_graph, done=done, active=set(active.values())
                ):
                    if len(active) >= jobs:
                        break
                    started += 1
                    logger.info(
                        f"[{started}/{len(pending)}] {function}: generating spec via claude -p"
                    )
                    future = pool.submit(
                        _verify_via_agent,
                        function,
                        file_path=file_path,
                        call_graph=call_graph,
                        timeout=timeout,
                        include_dirs=include_dirs,
                        keep_sessions=keep_sessions,
                    )
                    active[future] = function
            if not active:
                break
            finished, _ = wait(active, return_when=FIRST_COMPLETED)
            for future in finished:
                function = active.pop(future)
                result = future.result()
                results.append(result)
                _append_jsonl(log_path, result.to_record())
                done.add(function)
                logger.info(f"[{len(results)}/{len(pending)}] {function}: {result.outcome}")
                if result.outcome is GroundTruthVerificationResult.USAGE_LIMITED:
                    usage_limited.add(function)
                    logger.warning(
                        f"{function}: usage limit hit; no new sessions will start, "
                        f"{len(active)} in flight will finish and merge"
                    )
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
    return results, usage_limited


@dataclass(frozen=True)
class _RerunDecision:
    """Whether a function has earned another `claude -p` session, and why.

    Attributes:
        rerun (bool): True iff another session should be started.
        rationale (str): One sentence explaining the decision. When `rerun` is True it is also
            passed to the new session as a retry note, so the re-run is never a blind repeat.
    """

    rerun: bool
    rationale: str


def _should_rerun_session(
    sessions: list[ClaudeRun], attempts_after_each_session: list[int], *, improvable: bool
) -> _RerunDecision:
    """Decide whether to start another session for the current function.

    The attempt floor (`_MIN_VERIFICATION_ATTEMPTS_PER_SESSION`) exists so the loop does not
    advance on a session that barely tried. Re-running is only worth its cost, though, when the
    next session can plausibly do better than the last, and three kinds of session say it cannot:

    - one stopped by a usage limit: the account is throttled, and every further session would
      fail the same way (52 such re-runs were logged across the measured runs);
    - one that made no verification attempt on the file at all, when a previous session in this
      turn already did the same: the prompt, file and model are unchanged, so a third identical
      session is expected to end the same way (the measured case cost 2 x 1800 s);
    - a session after which the specification verifies, or after which there are no mutants to
      kill: there is no score left for a re-run to raise (`improvable` is False).

    Args:
        sessions (list[ClaudeRun]): The sessions run so far this turn, oldest first.
        attempts_after_each_session (list[int]): Verification attempts logged for the function
            after each of those sessions, cumulative over the turn.
        improvable (bool): Whether the specification can still be improved, per
            `is_spec_improvable_with_mutation_testing`.

    Returns:
        _RerunDecision: The decision, with a rationale suitable for the log and the retry prompt.
    """
    last = sessions[-1]
    attempts = attempts_after_each_session[-1]
    if attempts >= _MIN_VERIFICATION_ATTEMPTS_PER_SESSION:
        return _RerunDecision(False, "")
    if not improvable:
        return _RerunDecision(False, "")
    if len(sessions) >= _MAX_AGENT_SESSIONS_PER_FUNCTION:
        return _RerunDecision(False, f"session cap of {_MAX_AGENT_SESSIONS_PER_FUNCTION} reached")
    if _is_usage_limit_hit(last):
        return _RerunDecision(False, "the last session was stopped by a usage limit")
    # Sessions this turn that added no verification attempt at all.
    new_attempts = [
        after - (attempts_after_each_session[index - 1] if index else 0)
        for index, after in enumerate(attempts_after_each_session)
    ]
    sessions_without_attempt = sum(1 for count in new_attempts if count == 0)
    if new_attempts[-1] == 0 and sessions_without_attempt > _MAX_SESSIONS_WITHOUT_ATTEMPT:
        return _RerunDecision(
            False,
            f"{sessions_without_attempt} sessions in a row ended without running the verifier "
            "on this file; another identical session is not expected to",
        )
    how_it_ended = (
        f"was stopped by the harness after {_DEFAULT_CLAUDE_TIMEOUT_SEC} s"
        if last.timed_out
        else ("failed with an error" if last.is_error else "ended")
    )
    return _RerunDecision(
        True,
        f"the previous session {how_it_ended} after recording {new_attempts[-1]} verification "
        f"attempt(s) on this file, short of the {_MIN_VERIFICATION_ATTEMPTS_PER_SESSION} "
        "required; the function does not verify yet",
    )


def is_spec_improvable_with_mutation_testing(
    function: str, source_path: str, attempts_log_path: Path
) -> bool:
    """Return True iff a function's specification can be improved.

    Whether a specification can be improved (via Avocado) depends on the availability of mutants to
    kill; the kill score is the only metric that is provided to the agent. If no mutants are
    generated, and the function already verifies, there is no point going further.

    Args:
        function (str): The function under specification generation.
        source_path (str): The path to the source file where the function is declared.
        attempts_log_path (Path): The path to the log of verification attempts.

    Returns:
        bool: True iff a function's specification can be improved, i.e., it has mutants to kill
            and it is not successfully verified, yet.
    """
    function_has_mutants = False
    try:
        function_has_mutants = bool(get_mutants(source_path, function))
    except Exception:  # ruff: ignore[blind-except] - any generation failure should fall back to default behavior.
        logger.error(f"Failure in mutant generation for '{function}' in {source_path}")
        # Assume mutants exist in the worst-case scenario.
        function_has_mutants = True
    is_function_successfully_verified = _last_attempt_verified(attempts_log_path, function)
    return function_has_mutants and not is_function_successfully_verified


def _count_verification_attempts(log_path: Path, function: str) -> int:
    """Return the number of logged `avocado-run-cbmc` verification attempts for `function`.

    Reads the verification-attempts JSONL that `avocado-run-cbmc` appends to (one record per
    top-level verification attempt). A missing log, blank lines, and malformed records are treated
    as zero/skipped so counting never raises.

    Args:
        log_path (Path): Path to the verification-attempts JSONL log.
        function (str): The function whose attempts should be counted.

    Returns:
        int: The number of attempts recorded for `function`.
    """
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0
    count = 0
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            record = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if record.get("function") == function:
            count += 1
    return count


def _last_attempt_verified(log_path: Path, function: str) -> bool:
    """Return whether the most recent logged verification attempt for `function` succeeded.

    Reads the verification-attempts JSONL that `avocado-run-cbmc` appends to and returns the
    `verified` field of the last record naming `function`. That field is the *tool's* own CBMC
    verdict (`run_cbmc_and_mutation_testing._log_verification_attempt` records
    `result.is_function_verified`), not Claude's self-report, so it is safe to trust here. A missing
    log, blank lines, and malformed records are skipped; the function returns False when there is no
    decided record, so callers never skip work on the basis of a verification that did not happen.

    Args:
        log_path (Path): Path to the verification-attempts JSONL log.
        function (str): The function whose latest attempt should be inspected.

    Returns:
        bool: True iff the most recent attempt record for `function` reports verification success.
    """
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    verified = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            record = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if record.get("function") == function:
            verified = bool(record.get("verified", False))
    return verified


def _read_function_outcomes(log_path: Path) -> dict[str, str]:
    """Return the last recorded outcome for each function in the run log.

    Reads `<stem>-avocado-verify.jsonl`, ignoring blank/malformed lines and the terminal
    run-summary record (identified by a `"type"` key). Later records win, so the returned outcome
    reflects each function's most recent attempt. Never raises; a missing log yields an empty map.

    Args:
        log_path (Path): Path to the `<stem>-avocado-verify.jsonl` run log.

    Returns:
        dict[str, str]: Map of function name to its last recorded outcome string.
    """
    outcomes: dict[str, str] = {}
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return outcomes
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            record = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if "type" in record:  # terminal run-summary record, not a per-function result
            continue
        function = record.get("function")
        outcome = record.get("outcome")
        if function is not None and outcome is not None:
            outcomes[function] = outcome
    return outcomes


def _get_processed_functions(log_path: Path) -> set[str]:
    """Return the set of functions already "processed" according to the run log.

    A function is processed iff its most recent outcome is in `_PROCESSED_FUNCTION_OUTCOMES`.
    USAGE_LIMITED is excluded, so the function that hit a usage limit (and any never reached) is
    retried on resume.

    Args:
        log_path (Path): Path to the `avocado-verify.jsonl` run log.

    Returns:
        set[str]: Functions that should be skipped on resume.
    """
    return {
        function
        for function, outcome in _read_function_outcomes(log_path).items()
        if outcome in _PROCESSED_FUNCTION_OUTCOMES
    }


def _verified_functions(log_path: Path) -> set[str]:
    """Return the set of functions whose most recent recorded outcome is VERIFIED.

    Used for cross-run final accounting on resume, where the in-memory results of a single
    invocation do not reflect functions verified by earlier runs.

    Args:
        log_path (Path): Path to the `<stem>-avocado-verify.jsonl` run log.

    Returns:
        set[str]: Functions recorded as VERIFIED.
    """
    return {
        function
        for function, outcome in _read_function_outcomes(log_path).items()
        if outcome == GroundTruthVerificationResult.VERIFIED
    }


def _run_summary_record(
    status: str, *, done: list[str], remaining: list[str], verified: int, total: int
) -> dict:
    """Build the terminal run-summary record appended to the run log.

    Carries a reserved `"type": "run_summary"` key so readers distinguish it from per-function
    records (which have no `"type"`).

    Args:
        status (str): "usage_limited" when the run stopped early, "completed" otherwise.
        done (list[str]): Functions completed (terminal outcome) across all runs of this file.
        remaining (list[str]): Functions still pending, in topological order.
        verified (int): Count of functions verified across all runs.
        total (int): Total verifiable functions in the file.

    Returns:
        dict: A JSON-serializable terminal run-summary record.
    """
    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "type": "run_summary",
        "status": status,
        "done": done,
        "remaining": remaining,
        "verified": verified,
        "total": total,
    }


def _build_prompt(
    function: str,
    *,
    file_path: str,
    call_graph: CallGraph,
    include_dirs: list[str],
    retry_note: str = "",
) -> str:
    """Build the per-function prompt for a `claude -p` session.

    Besides naming the function and file, the prompt hands the agent what it would otherwise
    spend its first turns discovering: the exact `avocado-run-cbmc` command (with the include
    directories the harness detected, which the agent cannot guess), the in-file callees whose
    contracts will replace their bodies, and the in-file callers whose call sites must satisfy the
    preconditions being written. It also says that only runs of that command against that path
    count as attempts (in the measured runs a third of the agent's verifier calls were made on
    copies of the file the harness could not see), and that the session works in a private copy
    of which only the function's own definition and new top-level helpers are merged back.

    Args:
        function (str): The function to specify and verify.
        file_path (str): Absolute path to the C file defining the function.
        call_graph (CallGraph): Call graph of the file.
        include_dirs (list[str]): Include directories forwarded to `avocado-run-cbmc` as `-I`.
        retry_note (str): When this session is a re-run, why the previous one was not enough. It
            is put in front of the agent so the re-run starts differently from the session that
            failed.

    Returns:
        str: The prompt text.
    """
    command = ["avocado-run-cbmc", "--function", function, "--file", file_path]
    for include_dir in include_dirs:
        command += ["-I", include_dir]
    callees = get_in_file_callees_for(function, call_graph)
    callers = get_in_file_callers_of(function, call_graph)
    callees_text = ", ".join(callees) if callees else "none"
    callers_text = ", ".join(callers) if callers else "none"
    retry_text = (
        f"This is a re-run: {retry_note}. Run the command below on this file before anything "
        "else.\n\n"
        if retry_note
        else ""
    )
    return (
        f"{retry_text}"
        f"Verify {function} in {file_path}.\n"
        "\n"
        "Run exactly this command to verify it; on success it also reports the mutation kill "
        "score and the diff of every surviving mutant. Only runs of this command against exactly "
        "this path are recorded as verification attempts; runs on copies of the file are not.\n"
        "\n"
        f"    {shlex.join(command)}\n"
        "\n"
        "In-file callees (verified earlier; their contracts replace their bodies while this "
        f"function is verified): {callees_text}\n"
        "In-file callers (their call sites must satisfy the preconditions you write): "
        f"{callers_text}\n"
        "\n"
        "You are working in a private copy of the source directory. When this session ends, only "
        f"your changes to {function}'s definition (its contract and body) and any new top-level "
        "helper functions, declarations or macros you add to this file are kept; edits to other "
        "functions, to other files and to headers are discarded."
    )


def _build_claude_command(prompt: str, *, file_path: str, include_dirs: list[str]) -> list[str]:
    """Build the `claude -p` argument vector for one function.

    Claude is invoked non-interactively, so it cannot answer permission prompts;
    `--dangerously-skip-permissions` is the documented sandbox modality (see README). The
    C file's directory is granted with `--add-dir` so the file is reachable regardless of
    where the harness is invoked from; each include directory is granted the same way so the
    agent can read headers it needs.

    Claude Code's auto-memory is deliberately left enabled: each session is given a persistent
    notes directory (keyed by the repository's main worktree, so shared by every worktree of it)
    and its index is loaded into the session, so knowledge gained on one function or one run
    carries over to later ones. Note the consequence for experiments in `FINDINGS.md`: two arms
    run on one machine share those notes unless the experiment gives each arm its own directory.

    Args:
        prompt (str): The prompt to send (see `_build_prompt`).
        file_path (str): Absolute path to the C file being verified.
        include_dirs (list[str]): Extra include directories to expose via `--add-dir`.

    Returns:
        list[str]: The argument vector to hand to `subprocess.run`.
    """
    command = [
        "claude",
        "--print",
        prompt,
        "--output-format",
        "json",
        "--dangerously-skip-permissions",
        "--add-dir",
        str(Path(file_path).parent),
    ]
    for include_dir in include_dirs:
        command += ["--add-dir", include_dir]
    return command


def _run_claude(command: list[str], timeout: int) -> ClaudeRun:
    """Run a `claude -p` session as a subprocess and parse its JSON result.

    Args:
        command (list[str]): The claude argument vector.
        timeout (int): Per-session timeout in seconds.

    Returns:
        ClaudeRun: The parsed outcome, or a timeout sentinel if the session ran long.
    """
    logger.debug(f"running: {shlex.join(command)}")
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except TimeoutExpired:
        # It isn't possible to calculate some of the fields below when the timeout for a `claude -p`
        # command is reached.
        return ClaudeRun(
            returncode=_TIMEOUT_RETURNCODE,
            timed_out=True,
            is_error=True,
            session_id=None,
            result_text=f"claude -p timed out after {timeout}s",
            total_cost_usd=None,
            num_turns=None,
            duration_ms=None,
            subtype=None,
        )
    return _parse_claude_output(completed.returncode, completed.stdout, completed.stderr)


def _parse_claude_output(returncode: int, stdout: str, stderr: str) -> ClaudeRun:
    """Parse the JSON object emitted by `claude -p --output-format json`.

    Args:
        returncode (int): claude's exit code.
        stdout (str): Captured standard output (expected to be a single JSON object).
        stderr (str): Captured standard error (used for diagnostics on parse failure).

    Returns:
        ClaudeRun: The parsed session outcome.
    """
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        snippet = (stdout or stderr or "").strip()[:_MAX_PARSE_SNIPPET_CHARS]
        return ClaudeRun(
            returncode=returncode,
            timed_out=False,
            is_error=True,
            session_id=None,
            result_text=f"could not parse claude JSON output: {snippet}",
            total_cost_usd=None,
            num_turns=None,
            duration_ms=None,
            subtype=None,
        )
    return ClaudeRun(
        returncode=returncode,
        timed_out=False,
        is_error=bool(payload.get("is_error", returncode != 0)),
        session_id=payload.get("session_id"),
        result_text=str(payload.get("result", "")),
        total_cost_usd=payload.get("total_cost_usd"),
        num_turns=payload.get("num_turns"),
        duration_ms=payload.get("duration_ms"),
        subtype=payload.get("subtype"),
    )


def _is_usage_limit_hit(claude: ClaudeRun) -> bool:
    """Return True iff a `claude -p` session failed because of a usage/rate limit.

    A usage limit is a distinct, recoverable condition from an ordinary CLAUDE_ERROR: the work is
    not wrong, the account is throttled, and continuing to the next function would only burn more
    sessions against the same limit. Detection matches `result_text` (case-insensitively) against
    `_USAGE_LIMIT_RESULT_PATTERNS`; it is only meaningful when the session reported an error.

    Args:
        claude (ClaudeRun): The parsed session outcome.

    Returns:
        bool: True iff the session result indicates a usage/rate limit.
    """
    if not claude.is_error:
        return False
    text = claude.result_text.lower()
    return any(pattern in text for pattern in _USAGE_LIMIT_RESULT_PATTERNS)


def _outcome_for(claude: ClaudeRun, cbmc: RunCbmcResult) -> GroundTruthVerificationResult:
    """Combine the Claude session and CBMC verdict into a single outcome.

    A passing CBMC run is authoritative; otherwise a Claude-side timeout, usage limit, or error is
    surfaced ahead of a plain verification failure.

    Args:
        claude (ClaudeRun): The Claude session outcome.
        cbmc (RunCbmcResult): The independent CBMC verification result.

    Returns:
        GroundTruthVerificationResult: The overall per-function verdict.
    """
    if cbmc.is_function_verified:
        return GroundTruthVerificationResult.VERIFIED
    if claude.timed_out:
        return GroundTruthVerificationResult.CLAUDE_TIMED_OUT
    if _is_usage_limit_hit(claude):
        return GroundTruthVerificationResult.USAGE_LIMITED
    if claude.is_error:
        return GroundTruthVerificationResult.CLAUDE_ERROR
    return GroundTruthVerificationResult.UNVERIFIED


def _append_jsonl(path: Path, record: dict) -> None:
    """Append one record to a JSON Lines file.

    Args:
        path (Path): The JSONL file to append to.
        record (dict): The JSON-serializable record to write.
    """
    with _RUN_LOG_LOCK:
        try:
            with path.open("a", encoding="utf-8") as log_file:
                log_file.write(json.dumps(record) + "\n")
        except OSError:
            # Never let logging errors crash the tool.
            pass


def _log_summary(results: list[FunctionVerificationResult], log_path: Path) -> None:
    """Log a per-function summary and the location of the run log.

    Args:
        results (list[FunctionVerificationResult]): The per-function results, in run order.
        log_path (Path): Path to the JSONL run log.
    """
    logger.info("avocado-verify summary:")
    for result in results:
        logger.info(f"  {result.outcome!s:<17} {result.function}")
    verified = sum(
        1 for result in results if result.outcome is GroundTruthVerificationResult.VERIFIED
    )
    logger.info(f"{verified}/{len(results)} function(s) verified; log written to {log_path}")


def _autodetect_include_dirs(source_file: str) -> list[str]:
    """Return `[<source>/../include]` if that directory exists, else an empty list.

    Many CMake projects keep public headers in `<project>/include/` while sources live in
    `<project>/src/`. When that layout holds, returning the sibling `include/` directory lets
    CBMC resolve `#include "foo.h"` without the caller having to configure paths by hand.

    Args:
        source_file (str): Path to a `.c` file.

    Returns:
        list[str]: `[<resolved include dir>]` when present, else `[]`.
    """
    candidate = Path(source_file).resolve().parent.parent / "include"
    return [str(candidate)] if candidate.is_dir() else []


if __name__ == "__main__":
    main()
