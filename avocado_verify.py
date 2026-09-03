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
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from subprocess import TimeoutExpired

from loguru import logger

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

# Hard ceiling on how many `claude -p` sessions a single function may receive. Sessions beyond the
# first are granted only while the agent is demonstrably still improving the specification (see
# `_should_grant_another_session`), so this bounds the pathological case rather than setting the
# usual budget.
_MAX_AGENT_SESSIONS_PER_FUNCTION = 5

# How many consecutive sessions may leave both the specification and the kill score untouched
# before the harness concludes the agent is spinning and moves on. Two is the smallest value that
# still tolerates a single wasted session.
_PLATEAU_SESSIONS_BEFORE_STOPPING = 2


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
            harness moved on (>= 1; > 1 when the agent was still making progress).
        kill_score (float | None): The adjusted mutation kill score from the function's last
            mutation-tested attempt, or None if it was never mutation-tested. Recorded so a run log
            captures specification *strength*, not just pass/fail.
        raw_kill_score (float | None): The same, with presumed-equivalent mutants counted as
            survivors.
    """

    function: str
    outcome: GroundTruthVerificationResult
    claude_sessions: list[ClaudeRun]
    cbmc: RunCbmcResult
    internal_callees: list[str]
    verification_attempts: int
    agent_sessions: int
    kill_score: float | None = None
    raw_kill_score: float | None = None

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
            "kill_score": self.kill_score,
            "raw_kill_score": self.raw_kill_score,
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
        "--max-sessions",
        type=int,
        default=_MAX_AGENT_SESSIONS_PER_FUNCTION,
        metavar="N",
        help=(
            "Hard ceiling on `claude -p` sessions per function. Sessions beyond the first are "
            "granted only while the agent is still improving the specification "
            f"(default: {_MAX_AGENT_SESSIONS_PER_FUNCTION})."
        ),
    )
    parser.add_argument(
        "--plateau-limit",
        type=int,
        default=_PLATEAU_SESSIONS_BEFORE_STOPPING,
        metavar="N",
        help=(
            "Stop granting sessions once this many consecutive verification attempts have changed "
            "neither the specification nor the kill score "
            f"(default: {_PLATEAU_SESSIONS_BEFORE_STOPPING})."
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
            max_sessions=args.max_sessions,
            plateau_limit=args.plateau_limit,
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
    max_sessions: int = _MAX_AGENT_SESSIONS_PER_FUNCTION,
    plateau_limit: int = _PLATEAU_SESSIONS_BEFORE_STOPPING,
) -> FunctionVerificationResult:
    """Run one or more `claude -p` sessions for `function`, then re-verify it with CBMC.

    Sessions after the first are granted on evidence of progress rather than on a fixed attempt
    count: see `_should_grant_another_session`. The effect is that a function whose specification
    is still getting stronger keeps earning sessions up to `max_sessions`, while one whose agent
    has stalled is abandoned early instead of burning the full budget.

    Args:
        function (str): The function to specify and verify.
        file_path (str): Absolute path to the C file defining the function.
        call_graph (CallGraph): Call graph of the file, used to record in-file callees.
        timeout (int): Per-function timeout for the `claude -p` session, in seconds.
        include_dirs (list[str]): Extra include directories to expose to the agent and forward to
            CBMC's include search path.
        max_sessions (int): Hard ceiling on `claude -p` sessions for this function.
        plateau_limit (int): Consecutive unproductive verification attempts tolerated before the
            harness concludes the agent is spinning.

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
    previous_verification_attempts = len(_read_attempts(attempts_log_path, function))

    claude_sessions_for_function = [_run_claude(command, timeout)]
    while True:
        if _is_usage_limit_hit(claude_sessions_for_function[-1]):
            # The account is throttled, not the specification wrong. Retrying would spend the
            # whole session budget against the same limit; the caller stops the run instead.
            logger.warning(f"{function}: session stopped by a usage limit; not retrying")
            attempts = _read_attempts(attempts_log_path, function)
            decision = _SessionDecision(False, "stopped by a usage limit")
            break
        attempts = _read_attempts(attempts_log_path, function)
        decision = _should_grant_another_session(
            function, attempts, previous_verification_attempts, plateau_limit=plateau_limit
        )
        if not decision.grant:
            break
        if len(claude_sessions_for_function) >= max_sessions:
            logger.warning(
                f"{function}: {decision.rationale}, but the {max_sessions}-session ceiling is "
                "reached; moving on"
            )
            break
        logger.info(
            f"{function}: {decision.rationale}; re-running session "
            f"({len(claude_sessions_for_function) + 1}/{max_sessions})"
        )
        claude_sessions_for_function.append(_run_claude(command, timeout))

    sessions = len(claude_sessions_for_function)
    current_verification_attempts = len(attempts) - previous_verification_attempts
    logger.info(
        f"{function}: stopping after {sessions} session(s) and "
        f"{current_verification_attempts} verification attempt(s): {decision.rationale}"
    )

    # Objective verdict: re-run CBMC rather than trust Claude's self-report.
    cbmc = run_cbmc(function, file_path, include_dirs=include_dirs)
    return FunctionVerificationResult(
        function=function,
        outcome=_outcome_for(claude_sessions_for_function[-1], cbmc),
        claude_sessions=claude_sessions_for_function,
        cbmc=cbmc,
        internal_callees=call_graph.get_callees(function).internal,
        verification_attempts=current_verification_attempts,
        agent_sessions=sessions,
        kill_score=_latest_kill_score(attempts),
        raw_kill_score=_latest_kill_score(attempts, raw=True),
    )


@dataclass(frozen=True)
class _SessionDecision:
    """Whether to spend another `claude -p` session on a function, and why.

    Attributes:
        grant (bool): True iff another session should be run.
        rationale (str): Human-readable justification, logged either way.
    """

    grant: bool
    rationale: str


def _should_grant_another_session(
    function: str,
    attempts: list[dict],
    previous_attempts: int,
    *,
    plateau_limit: int = _PLATEAU_SESSIONS_BEFORE_STOPPING,
) -> _SessionDecision:
    """Decide whether `function` has earned another agent session.

    The old rule counted how many times the agent ran `avocado-run-cbmc` and stopped at a fixed
    cap, which both cut off agents that were still strengthening a specification and kept paying
    for agents that had stalled. This decides on evidence instead, reading the verification-attempts
    log that `avocado-run-cbmc` writes:

    - An agent that has not yet reached the attempt floor gets another session — it barely tried.
    - An agent that verified the function and left no killable mutants is done; there is no score
      left to raise.
    - An agent whose last attempt raised the kill score, changed the specification, or newly
      verified the function is making progress and gets another session.
    - An agent whose last `plateau_limit` attempts changed neither the specification nor the score
      is spinning, and is stopped.

    Args:
        function (str): The function under verification, for the rationale text.
        attempts (list[dict]): All attempt records logged for the function, oldest first.
        previous_attempts (int): How many of those predate this harness turn.
        plateau_limit (int): Consecutive unproductive attempts tolerated before stopping.

    Returns:
        _SessionDecision: Whether to grant another session, with the reason.
    """
    attempts_this_turn = attempts[previous_attempts:]
    if len(attempts_this_turn) < _MIN_VERIFICATION_ATTEMPTS_PER_SESSION:
        return _SessionDecision(
            True,
            f"agent attempted verification {len(attempts_this_turn)}/"
            f"{_MIN_VERIFICATION_ATTEMPTS_PER_SESSION} time(s)",
        )

    latest = attempts_this_turn[-1]
    if not latest.get("verified"):
        # Still failing to verify: more attempts are the only way forward, and the plateau check
        # below cannot apply because there is no kill score yet.
        if _is_plateaued(attempts_this_turn, plateau_limit):
            return _SessionDecision(
                False, f"{function} still unverified and the last {plateau_limit} attempts changed "
                "neither the specification nor the outcome"
            )
        return _SessionDecision(True, f"{function} does not verify yet")

    mutation = latest.get("mutation")
    if not isinstance(mutation, dict):
        # Verified, but nothing was mutation-tested (no mutable operators), so there is no kill
        # score to improve and another session cannot strengthen the specification.
        return _SessionDecision(False, f"{function} verified with no mutants to kill")

    if not _has_live_mutants(mutation):
        return _SessionDecision(
            False, f"{function} verified and every killable mutant is accounted for"
        )

    if _is_plateaued(attempts_this_turn, plateau_limit):
        return _SessionDecision(
            False,
            f"kill score and specification unchanged across the last {plateau_limit} attempt(s)",
        )
    return _SessionDecision(
        True, f"{function} still has {mutation.get('survived', 0)} live surviving mutant(s)"
    )


def _has_live_mutants(mutation: dict) -> bool:
    """Return True iff a mutation summary still shows mutants worth trying to kill.

    Timed-out, compile-failed, instrumentation-failed, and presumed-equivalent mutants are all
    excluded: none of them can be turned into a kill by strengthening the specification.

    Args:
        mutation (dict): A `MutationScore.summary()` record from the attempts log.

    Returns:
        bool: True iff any surviving, killable mutant remains.
    """
    try:
        return int(mutation.get("survived", 0)) > 0
    except (TypeError, ValueError):
        return False


def _is_plateaued(attempts: list[dict], plateau_limit: int) -> bool:
    """Return True iff the last `plateau_limit` attempts made no discernible progress.

    Progress means any of: the specification changed (a different `spec_digest`), the kill score
    moved, or the pass/fail verdict changed. `spec_digest` is read from the mutation summary, so
    attempts that never reached mutation testing fall back to comparing verdicts alone.

    Args:
        attempts (list[dict]): Attempt records for the function, oldest first.
        plateau_limit (int): How many consecutive unproductive attempts constitute a plateau.

    Returns:
        bool: True iff the agent appears to be spinning.
    """
    if len(attempts) < plateau_limit + 1:
        return False
    window = attempts[-(plateau_limit + 1) :]
    signatures = {
        (
            bool(record.get("verified")),
            _kill_score_of(record),
            (record.get("mutation") or {}).get("spec_digest"),
        )
        for record in window
    }
    return len(signatures) == 1


def _kill_score_of(attempt: dict, *, raw: bool = False) -> float | None:
    """Return the kill score recorded on one attempt, or None when it was not mutation-tested.

    Args:
        attempt (dict): An attempt record from the verification-attempts log.
        raw (bool): When True, return the raw score (presumed-equivalent mutants counted as
            survivors) instead of the adjusted one.

    Returns:
        float | None: The recorded score, or None if absent or unparseable.
    """
    mutation = attempt.get("mutation")
    if not isinstance(mutation, dict):
        return None
    try:
        return float(mutation["raw_kill_score" if raw else "kill_score"])
    except (KeyError, TypeError, ValueError):
        return None


def _latest_kill_score(attempts: list[dict], *, raw: bool = False) -> float | None:
    """Return the most recently recorded kill score for a function.

    Args:
        attempts (list[dict]): Attempt records for the function, oldest first.
        raw (bool): When True, return the raw rather than the adjusted score.

    Returns:
        float | None: The latest recorded score, or None when none was recorded.
    """
    for attempt in reversed(attempts):
        if (score := _kill_score_of(attempt, raw=raw)) is not None:
            return score
    return None


def _read_attempts(log_path: Path, function: str) -> list[dict]:
    """Return every logged `avocado-run-cbmc` verification attempt for `function`, oldest first.

    Reads the verification-attempts JSONL that `avocado-run-cbmc` appends to, one record per
    top-level invocation. Each record carries the *tool's* own CBMC verdict (`verified`) and, when
    mutation testing ran, a `mutation` summary with the kill scores and the specification digest —
    which is what lets the harness tell a productive session from a stalled one. A missing log,
    blank lines, and malformed records are skipped so reading never raises.

    Args:
        log_path (Path): Path to the verification-attempts JSONL log.
        function (str): The function whose attempts should be returned.

    Returns:
        list[dict]: The attempt records naming `function`, in log order.
    """
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    attempts: list[dict] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            record = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict) and record.get("function") == function:
            attempts.append(record)
    return attempts


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
