#!/usr/bin/env python3

"""Generate and verify a program with Avocado.

Calls Claude Code over the functions of a C file in callee-first topological order.

Verifying callees before their callers means subsequent sessions have access to previously-generated
specifications (i.e., session to verify callers have access to any callee specs).

For each function the harness runs a fresh `claude -p` session prompting Claude to generate a CBMC
specification for a function. Once Claude reports it is finished, this harness independently runs
CBMC to record a ground-truth verification result.

Usage:
    % avocado-verify --file <PATH_TO_C_FILE> \
        [--claude-timeout <TIMEOUT>] \
        [--resume-from <PATH_TO_JSONL_LOG>]
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

from loguru import logger

from eval.mutants.mutate_function import get_mutants
from tools.construct_call_graph import construct_call_graph
from tools.get_topological_ordering_of_functions import get_topological_ordering_of_functions
from tools.run_cbmc import RunCbmcResult, run_cbmc
from tools.run_cbmc_and_mutation_testing import VERIFICATION_ATTEMPTS_LOG_SUFFIX
from tools.util.callgraph import CallGraph

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
        total_cost_usd (float | None): session cost in USD, when reported.
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
        claude (ClaudeRun): The last `claude -p` session that (re)wrote the specification.
        cbmc (RunCbmcResult): The independent CBMC verification the harness ran afterward.
        internal_callees (list[str]): The function's in-file callees, for the run log.
        verification_attempts (int): How many times the agent attempted verification (ran
            `avocado-run-cbmc`) for this function across all its sessions.
        agent_sessions (int): How many `claude -p` sessions this function received before the
            harness moved on (>= 1; > 1 when re-runs were needed to reach the attempt floor).
    """

    function: str
    outcome: GroundTruthVerificationResult
    claude: ClaudeRun
    cbmc: RunCbmcResult
    internal_callees: list[str]
    verification_attempts: int
    agent_sessions: int

    def to_record(self) -> dict:
        """Return a JSON-serializable record of this result for the run log.

        Returns:
            dict: A timestamped record capturing the Claude session and CBMC verdict.
        """
        return {
            "timestamp": datetime.now(UTC).isoformat(),
            "function": self.function,
            "outcome": str(self.outcome),
            "internal_callees": self.internal_callees,
            "verification_attempts": self.verification_attempts,
            "agent_sessions": self.agent_sessions,
            "claude": {
                "returncode": self.claude.returncode,
                "timed_out": self.claude.timed_out,
                "is_error": self.claude.is_error,
                "session_id": self.claude.session_id,
                "total_cost_usd": self.claude.total_cost_usd,
                "num_turns": self.claude.num_turns,
                "duration_ms": self.claude.duration_ms,
                "subtype": self.claude.subtype,
                "result": self.claude.result_text,
            },
            "cbmc": {
                "verdict": str(self.cbmc),
                "is_function_verified": self.cbmc.is_function_verified,
                "returncode": self.cbmc.returncode,
                "timed_out": self.cbmc.timed_out,
                "failed_step": None
                if self.cbmc.failed_step is None
                else self.cbmc.failed_step.value,
            },
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
    args = parser.parse_args()

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

    results: list[FunctionVerificationResult] = []
    for index, function in enumerate(pending, start=1):
        logger.info(f"[{index}/{len(pending)}] {function}: generating spec via claude -p")
        verification_result_from_agent = _verify_via_agent(
            function,
            file_path=str(file_path),
            call_graph=call_graph,
            timeout=args.claude_timeout,
            include_dirs=include_dirs,
        )
        results.append(verification_result_from_agent)
        _append_jsonl(log_path, verification_result_from_agent.to_record())
        logger.info(
            f"[{index}/{len(pending)}] {function}: {verification_result_from_agent.outcome}"
        )

        if verification_result_from_agent.outcome is GroundTruthVerificationResult.USAGE_LIMITED:
            remaining = pending[index:]
            logger.warning(
                f"{function}: stopped after {index}/{len(pending)} function(s) this run due to a "
                f"usage limit; {len(remaining)} remaining. Resume with --resume-from."
            )
            _log_summary(results, log_path)
            _finalize_run(
                log_path,
                functions=functions,
                remaining=[function, *remaining],
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


def _verify_via_agent(
    function: str,
    *,
    file_path: str,
    call_graph: CallGraph,
    timeout: int,
    include_dirs: list[str],
) -> FunctionVerificationResult:
    """Run one or more `claude -p` sessions for `function`, then re-verify it with CBMC.

    Re-runs the session until the agent has attempted verification at least
    `_MIN_VERIFICATION_ATTEMPTS` times (capped at `_MAX_AGENT_SESSIONS_PER_FUNCTION`) before the
    caller advances to the next function.

    Args:
        function (str): The function to specify and verify.
        file_path (str): Absolute path to the C file defining the function.
        call_graph (CallGraph): Call graph of the file, used to record in-file callees.
        timeout (int): Per-function timeout for the `claude -p` session, in seconds.
        include_dirs (list[str]): Extra include directories to expose to the agent and forward to
            CBMC's include search path.

    Returns:
        FunctionVerificationResult: The combined Claude/CBMC outcome for the function.
    """
    prompt = f"Verify {function} in {file_path}"
    command = _build_claude_command(prompt, file_path=file_path, include_dirs=include_dirs)
    attempts_log_path = Path(file_path).with_name(
        f"{Path(file_path).stem}{VERIFICATION_ATTEMPTS_LOG_SUFFIX}"
    )

    # Attempts already logged for this function before this turn (e.g. by an earlier function's
    # session that also exercised this one); gate only on attempts made from here forward.
    previous_verification_attempts = _count_verification_attempts(attempts_log_path, function)

    # Do not advance to the next function until the agent has attempted verification at least
    # `_MIN_VERIFICATION_ATTEMPTS` times, re-running the session up to a capped number of times.
    claude = _run_claude(command, timeout)
    sessions = 1
    current_verification_attempts = (
        _count_verification_attempts(attempts_log_path, function) - previous_verification_attempts
    )
    while (
        current_verification_attempts < _MIN_VERIFICATION_ATTEMPTS_PER_SESSION
        and sessions < _MAX_AGENT_SESSIONS_PER_FUNCTION
        and is_spec_improvable_with_mutation_testing(function, file_path, attempts_log_path)
    ):
        logger.warning(
            f"{function}: agent attempted verification "
            f"{current_verification_attempts}/{_MIN_VERIFICATION_ATTEMPTS_PER_SESSION} time(s); "
            f"re-running session ({sessions + 1}/{_MAX_AGENT_SESSIONS_PER_FUNCTION})"
        )
        claude = _run_claude(command, timeout)
        sessions += 1
        current_verification_attempts = (
            _count_verification_attempts(attempts_log_path, function)
            - previous_verification_attempts
        )
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

    # Objective verdict: re-run CBMC rather than trust Claude's self-report.
    cbmc = run_cbmc(function, file_path, include_dirs=include_dirs)
    return FunctionVerificationResult(
        function=function,
        outcome=_outcome_for(claude, cbmc),
        claude=claude,
        cbmc=cbmc,
        internal_callees=call_graph.get_callees(function).internal,
        verification_attempts=current_verification_attempts,
        agent_sessions=sessions,
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
    except Exception:  # noqa: BLE001 - any generation failure should fall back to default behavior.
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


def _build_claude_command(prompt: str, *, file_path: str, include_dirs: list[str]) -> list[str]:
    """Build the `claude -p` argument vector for one function.

    Claude is invoked non-interactively, so it cannot answer permission prompts;
    `--dangerously-skip-permissions` is the documented sandbox modality (see README). The
    C file's directory is granted with `--add-dir` so the file is reachable regardless of
    where the harness is invoked from; each include directory is granted the same way so the
    agent can read headers it needs.

    Args:
        prompt (str): The prompt to send (currently blank).
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
