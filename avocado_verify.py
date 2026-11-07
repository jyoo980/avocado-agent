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
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from loguru import logger

from tools.construct_call_graph import construct_call_graph
from tools.get_topological_ordering_of_functions import get_topological_ordering_of_functions
from tools.run_cbmc import CbmcStep, RunCbmcResult, list_compiled_functions, verify_function
from tools.run_cbmc_and_mutation_testing import VERIFICATION_ATTEMPTS_LOG_SUFFIX
from tools.util import run_command
from tools.util.callgraph import CallGraph

# Per-function wall-clock budget for a single `claude -p` session. A session may run CBMC
# several times (each with its own multi-minute timeout) across the coverage and quality
# passes, so this default is deliberately generous. The budget is wall-clock time, so it also
# counts the time those CBMC runs spend waiting for a shared subprocess slot (see
# `tools.run_cbmc.run_shell_command`) while other processes use the slots.
_DEFAULT_CLAUDE_TIMEOUT_SEC = 1800

# GNU `timeout(1)` convention, matching `tools.run_cbmc`: distinguishes a timeout from an
# ordinary non-zero exit in both the recorded return code and the run log.
_TIMEOUT_RETURNCODE = 124

# 0 indicates all functions have been verified, 1 indicates some functions may not be verified.
_EXIT_ALL_VERIFIED = 0
_EXIT_SOME_UNVERIFIED = 1
# 2 indicates an early stop due to a usage limit being hit.
_EXIT_USAGE_LIMITED = 2

# `main` is never specified (see AGENTS.md); skip it wherever it appears in the ordering.
_UNVERIFIABLE_FUNCTIONS = frozenset({"main"})

# Cap on the raw stdout/stderr snippet kept when Claude's JSON output cannot be parsed.
_MAX_PARSE_SNIPPET_CHARS = 500

# Case-insensitive substrings in claude's `result` text that indicate the session was stopped by a
# usage/rate limit rather than a genuine task failure. How a usage limit surfaces in
# `claude -p --output-format json` is not formally documented, so detection is a text match kept in
# one place; adjust these as the CLI's wording evolves.
_USAGE_LIMIT_RESULT_PATTERNS = ("usage limit reached", "rate limit", "resets ")

# The loop re-runs a function's `claude -p` session until the agent has *attempted* verification
# (run `avocado-run-cbmc`) at least this many times for the function, summed over the function's
# sessions and counted from the verification-attempts log. This guards against advancing on a
# session that barely tried. The loop advances without reaching this floor if the function
# verifies, if a session times out or hits a usage limit, or after
# `_MAX_AGENT_SESSIONS_PER_FUNCTION` sessions.
_MIN_VERIFICATION_ATTEMPTS_PER_FUNCTION = 2

# Upper bound on how many `claude -p` sessions a single function receives while trying to reach
# `_MIN_VERIFICATION_ATTEMPTS_PER_FUNCTION`. Prevents an unproductive session from looping forever;
# once hit, the harness proceeds anyway and records the shortfall.
_MAX_AGENT_SESSIONS_PER_FUNCTION = 3

# Sets the max number of times a session is re-run when it initially ends without a verification
# attempt (i.e., a CBMC execution). A session that fails with a non-timeout error does not count
# toward this value.
#
# A second session that ends without a verifiation attempt leans toward evidence that the agent is
# not able to make progress, and a third session would likely be a waste of time.
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
        cbmc (RunCbmcResult): The independent CBMC verification the harness ran after the last
            session.
        internal_callees (list[str]): The function's in-file callees, for the run log.
        verification_attempts (int): How many times the agent attempted verification (ran
            `avocado-run-cbmc`) for this function across all its sessions.
        agent_sessions (int): How many `claude -p` sessions this function received before the
            harness moved on (>= 1; > 1 when re-runs were needed to reach the attempt floor).
        not_in_build (bool): True iff the function is in the source text but not in the compiled
            program (the preprocessor removes it), so it was given no session and no CBMC run
            (see `_functions_not_in_build`). It is recorded as unverified.
    """

    function: str
    outcome: GroundTruthVerificationResult
    claude_sessions: list[ClaudeRun]
    cbmc: RunCbmcResult
    internal_callees: list[str]
    verification_attempts: int
    agent_sessions: int
    not_in_build: bool = False

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
                "no_contract": self.cbmc.no_contract,
                "contract_probe_failed": self.cbmc.contract_probe_failed,
                "returncode": self.cbmc.returncode,
                "timed_out": self.cbmc.timed_out,
                "failed_step": None
                if self.cbmc.failed_step is None
                else self.cbmc.failed_step.value,
            },
            "not_in_build": self.not_in_build,
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
        attempts_log_path = file_path.with_name(
            f"{file_path.stem}{VERIFICATION_ATTEMPTS_LOG_SUFFIX}"
        )
        attempts_log_path.write_text("", encoding="utf-8")  # Scope the attempts log to this run.

    # Functions the preprocessor removes cannot be verified and are never really called, so they
    # get no session and are taken out of every other function's callees.
    not_in_build = _functions_not_in_build(functions, str(file_path), include_dirs)
    if not_in_build:
        logger.warning(
            f"{len(not_in_build)} function(s) are in the source but not in the compiled program "
            f"and are recorded as unverified without a session: {', '.join(not_in_build)}"
        )
        call_graph = _without_functions(call_graph, not_in_build)
        for function in not_in_build:
            if function not in already_done:
                _append_jsonl(log_path, _not_in_build_result(function).to_record())
                already_done.add(function)

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
            log_path,
            functions=functions,
            remaining=[],
            status="completed",
            stopped_early=False,
            not_in_build=not_in_build,
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
                not_in_build=not_in_build,
            )

    _log_summary(results, log_path)
    _finalize_run(
        log_path,
        functions=functions,
        remaining=[],
        status="completed",
        stopped_early=False,
        not_in_build=not_in_build,
    )


def _functions_not_in_build(
    functions: list[str], file_path: str, include_dirs: list[str]
) -> list[str]:
    """Return the functions that are in the source text but not in the compiled program.

    The call graph is read from the source without preprocessing, so it lists functions inside
    conditional blocks that this build leaves out. CBMC has no such function: a session for one
    cannot succeed, and a caller whose callee list names one fails at `goto-instrument`.

    Args:
        functions (list[str]): The verifiable functions found in the source text.
        file_path (str): Absolute path to the C file.
        include_dirs (list[str]): Include directories forwarded to `goto-cc`.

    Returns:
        list[str]: The functions the compiler does not build, in the order of `functions`; empty
            when the file could not be compiled, since nothing is then known.
    """
    compiled = list_compiled_functions(file_path, include_dirs)
    if compiled is None:
        logger.warning(
            f"could not list the functions compiled from {file_path}; treating every function "
            "in the source as part of the build"
        )
        return []
    return [function for function in functions if function not in compiled]


def _without_functions(call_graph: CallGraph, removed: list[str]) -> CallGraph:
    """Return a call graph without the given functions, as nodes and as callees.

    Args:
        call_graph (CallGraph): A call graph.
        removed (list[str]): Functions to take out.

    Returns:
        CallGraph: A new call graph in which no function is, or calls, one of `removed`.
    """
    gone = set(removed)
    return CallGraph(
        {
            function: {
                "internal": [callee for callee in callees.internal if callee not in gone],
                "external": list(callees.external),
            }
            for function, callees in call_graph.items()
            if function not in gone
        }
    )


def _not_in_build_result(function: str) -> FunctionVerificationResult:
    """Return the result recorded for a function that is not in the compiled program.

    It is recorded the way such a function ended up before it was recognised -- unverified,
    because goto-cc finds no such function -- but without the session that used to be spent
    finding that out, and marked `not_in_build`.

    Args:
        function (str): A function the compiler does not build.

    Returns:
        FunctionVerificationResult: An unverified result with no sessions and no cost.
    """
    return FunctionVerificationResult(
        function=function,
        outcome=GroundTruthVerificationResult.UNVERIFIED,
        claude_sessions=[],
        cbmc=RunCbmcResult(
            function=function,
            failed_step=CbmcStep.GOTO_CC,
            timed_out=False,
            returncode=1,
            response=f"{function} is not in the compiled program: the preprocessor removes it",
        ),
        internal_callees=[],
        verification_attempts=0,
        agent_sessions=0,
        not_in_build=True,
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
    not_in_build: list[str] | None = None,
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
        not_in_build (list[str] | None): Functions that are not in the compiled program. They
            are part of `functions` and count as unverified; the summary names them.
    """
    verified_set = _verified_functions(log_path)
    verified = sum(1 for function in functions if function in verified_set)
    done = [function for function in functions if function not in remaining]
    _append_jsonl(
        log_path,
        _run_summary_record(
            status,
            done=done,
            remaining=remaining,
            verified=verified,
            total=len(functions),
            not_in_build=not_in_build,
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
    """Run one or more `claude -p` sessions for `function`, re-verifying it with CBMC after each.

    Re-runs the session while `_should_rerun_session` says another one can do better.

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

    # Attempts already logged before this turn (e.g. by an earlier function's session that also
    # exercised this one); gate only on attempts made from here forward.
    records = _read_jsonl_records(attempts_log_path)
    previous_verification_attempts = _count_verification_attempts(records, function)
    previous_file_verification_attempts = _count_verification_attempts(records)

    # Do not advance to the next function until the agent has attempted verification at least
    # `_MIN_VERIFICATION_ATTEMPTS_PER_FUNCTION` times, re-running the session up to a capped number
    # of times -- but only while a re-run can plausibly do better than the session before it (see
    # `_should_rerun_session`).
    # The harness decides whether the function verifies by running CBMC itself after each session,
    # not from the `verified` field of the attempts log. A verified record in the log is stale if
    # the agent edited a specification after that attempt, and a function that an earlier
    # function's session verified might get no attempt at all in its own sessions, because
    # AGENTS.md tells the agent not to re-verify a function whose specification has not changed.
    claude_sessions_for_function: list[ClaudeRun] = []
    attempts_on_function_after_each_session: list[int] = []
    attempts_on_any_function_after_each_session: list[int] = []
    while True:
        claude_sessions_for_function.append(_run_claude(command, timeout))
        records = _read_jsonl_records(attempts_log_path)
        attempts_on_function_after_each_session.append(
            _count_verification_attempts(records, function) - previous_verification_attempts
        )
        attempts_on_any_function_after_each_session.append(
            _count_verification_attempts(records) - previous_file_verification_attempts
        )
        # Objective verdict: re-run CBMC rather than trust Claude's self-report.
        cbmc = verify_function(function, file_path, include_dirs=include_dirs)
        verified = cbmc.is_function_verified
        decision = _should_rerun_session(
            claude_sessions_for_function,
            attempts_on_function_after_each_session,
            attempts_on_any_function_after_each_session=(
                attempts_on_any_function_after_each_session
            ),
            verified=verified,
            timeout=timeout,
        )
        if not decision.rerun:
            break
        logger.warning(
            f"{function}: re-running session "
            f"({len(claude_sessions_for_function) + 1}/{_MAX_AGENT_SESSIONS_PER_FUNCTION}): "
            f"{decision.rationale}"
        )
    sessions = len(claude_sessions_for_function)
    current_verification_attempts = attempts_on_function_after_each_session[-1]
    logger.info(f"{function}: not re-running: {decision.rationale}")
    # A specification that verifies stopped the loop deliberately, not short of the floor.
    if not verified and current_verification_attempts < _MIN_VERIFICATION_ATTEMPTS_PER_FUNCTION:
        logger.warning(
            f"{function}: proceeding after {sessions} session(s) with only "
            f"{current_verification_attempts}/{_MIN_VERIFICATION_ATTEMPTS_PER_FUNCTION} "
            f"verification attempt(s)"
        )

    return FunctionVerificationResult(
        function=function,
        outcome=_outcome_for(claude_sessions_for_function[-1], cbmc),
        claude_sessions=claude_sessions_for_function,
        cbmc=cbmc,
        internal_callees=call_graph.get_callees(function).internal,
        verification_attempts=current_verification_attempts,
        agent_sessions=sessions,
    )


@dataclass(frozen=True)
class _RerunDecision:
    """The decision whether a function gets another `claude -p` session, and the reason for it.

    Attributes:
        rerun (bool): True iff another session should be started.
        rationale (str): One sentence explaining the decision, for the log.
    """

    rerun: bool
    rationale: str


def _should_rerun_session(
    sessions_verifying_current_function: list[ClaudeRun],
    attempts_on_function_after_each_session: list[int],
    *,
    attempts_on_any_function_after_each_session: list[int],
    verified: bool,
    timeout: int,
) -> _RerunDecision:
    """Return the decision whether to start another session for the current function.

    The attempt floor (`_MIN_VERIFICATION_ATTEMPTS_PER_FUNCTION`) exists so the loop does not
    advance on a session that barely tried. Re-running should only occur when the next session can
    plausibly do better than the last.

    Here are conditions under which a rerun should *not* occur:

    - one stopped by a usage limit: the account is throttled, and every further session would fail
      the same way.
    - one stopped by the harness's timeout: another session would likely time out again;
    - one that made no verification attempt on the file at all, when the session before it did the
      same: the prompt, file and model are unchanged, so a third identical session is expected to
      end the same way. A session that failed with an error is ignored.
    - a session after which the specification verifies: there is nothing left for a re-run to
      achieve (`verified` is True).

    Args:
        sessions_verifying_current_function (list[ClaudeRun]): a list of the `claude -p` sessions
            so far in a function's turn, oldest first.
        attempts_on_function_after_each_session (list[int]): the number of verification
            attempts on the function logged after each of the sessions, cumulative over the turn.
        attempts_on_any_function_after_each_session (list[int]): the number of verification
            attempts on any function in the file logged after each of the sessions, cumulative
            over the turn. Attempts on callees count, so a session that works on a callee's
            contract is not mistaken for one that never ran the verifier.
        verified (bool): True if CBMC verifies the function after the last of the sessions.
        timeout (int): the per-session timeout of the sessions, in seconds; used for diagnostics.

    Returns:
        _RerunDecision: The decision, with a one-sentence rationale.
    """
    last = sessions_verifying_current_function[-1]
    attempts = attempts_on_function_after_each_session[-1]
    if attempts >= _MIN_VERIFICATION_ATTEMPTS_PER_FUNCTION:
        return _RerunDecision(
            False,
            f"the function has {attempts} verification attempt(s), meeting the "
            f"{_MIN_VERIFICATION_ATTEMPTS_PER_FUNCTION} required",
        )
    if verified:
        return _RerunDecision(False, "the specification verifies")
    if _is_usage_limit_hit(last):
        return _RerunDecision(False, "the last session was stopped by a usage limit")
    if last.timed_out:
        return _RerunDecision(
            False,
            f"the last session was stopped by the harness after {timeout} s; another would "
            "likely time out too",
        )
    if len(sessions_verifying_current_function) >= _MAX_AGENT_SESSIONS_PER_FUNCTION:
        return _RerunDecision(False, f"session cap of {_MAX_AGENT_SESSIONS_PER_FUNCTION} reached")
    # The most recent sessions, in a row, that added no verification attempt on the file at all,
    # ignoring sessions that failed with an error.
    attempts_on_any_function_by_each_session = _attempts_added_by_each_session(
        attempts_on_any_function_after_each_session
    )
    sessions_without_attempt = 0
    for session, count in zip(
        reversed(sessions_verifying_current_function),
        reversed(attempts_on_any_function_by_each_session),
        strict=True,
    ):
        if count:
            break
        if not session.is_error:
            sessions_without_attempt += 1
    if sessions_without_attempt > _MAX_SESSIONS_WITHOUT_ATTEMPT:
        return _RerunDecision(
            False,
            f"{sessions_without_attempt} sessions in a row ran without an error and ended "
            "without running the verifier on this file; another identical session is not "
            "expected to",
        )
    how_it_ended = "failed with an error" if last.is_error else "ended"
    not_verified = "; the function does not verify yet" if attempts else ""
    return _RerunDecision(
        True,
        f"the previous session {how_it_ended}; it recorded "
        f"{_attempts_added_by_each_session(attempts_on_function_after_each_session)[-1]} "
        f"verification attempt(s) on this function, for {attempts} in "
        f"{len(sessions_verifying_current_function)} session(s), short of the "
        f"{_MIN_VERIFICATION_ATTEMPTS_PER_FUNCTION} required{not_verified}",
    )


def _attempts_added_by_each_session(attempts_after_each_session: list[int]) -> list[int]:
    """Return how many verification attempts each session made, given the running totals.

    For example, `_attempts_added_by_each_session([0, 2, 3])` is `[0, 2, 1]`: the first session
    made no attempt, the second made 2 and the third made 1.

    Args:
        attempts_after_each_session (list[int]): the total number of attempts logged after each
            session, oldest first.

    Returns:
        list[int]: The number of attempts that each session added to the total.
    """
    return [
        after - (attempts_after_each_session[index - 1] if index else 0)
        for index, after in enumerate(attempts_after_each_session)
    ]


def _read_jsonl_records(log_path: Path) -> list[dict[str, object]]:
    """Return the JSON objects in the given JSONL log, oldest first.

    A missing or unreadable log yields no records. A line that is blank, is not UTF-8, is not JSON,
    or is JSON but not an object (such as `[]`) is skipped, so one corrupt line does not discard
    the rest of the log.

    Args:
        log_path (Path): a path to a JSONL log, such as a verification-attempts log or a run log.

    Returns:
        list[dict[str, object]]: The records, each parsed from one line of the log.

    """
    try:
        lines = log_path.read_bytes().splitlines()
    except OSError:
        return []
    records = []
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:  # `UnicodeDecodeError` or `json.JSONDecodeError`
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def _count_verification_attempts(
    records: list[dict[str, object]], function: str | None = None
) -> int:
    """Return the number of verification attempts on the given function among the given records.

    If `function` is None, return the number of attempts on every function.

    Args:
        records (list[dict[str, object]]): a list of records from a verification-attempts log, as
            returned by `_read_jsonl_records`. The log is the JSONL file that `avocado-run-cbmc`
            appends to, with one record per top-level verification attempt, such as
            `{"function": "foo", "verified": true, ...}`.
        function (str | None): the function whose attempts to count, or None to count the attempts
            on every function.

    Returns:
        int: The number of the records for `function`, or of all the records if `function` is None.
    """
    return sum(1 for record in records if function is None or record.get("function") == function)


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
    for record in _read_jsonl_records(log_path):
        if "type" in record:  # terminal run-summary record, not a per-function result
            continue
        function = record.get("function")
        outcome = record.get("outcome")
        if isinstance(function, str) and isinstance(outcome, str):
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
    status: str,
    *,
    done: list[str],
    remaining: list[str],
    verified: int,
    total: int,
    not_in_build: list[str] | None = None,
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
        not_in_build (list[str] | None): Functions that are in the source but not in the
            compiled program. They are included in `total` and are not verified.

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
        "not_in_build": list(not_in_build or []),
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
        list[str]: The argument vector to hand to `run_command`.
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
    completed = run_command(command, timeout)
    if completed is None:
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
