"""Run CBMC on a function and perform mutation testing.

Usage:
    % avocado-run-cbmc --function <FUNCTION_NAME> \
                       --file <PATH_TO_C_FILE> \
                       [-I <PATH_TO_INCLUDE_DIR(S)>]...

The CBMC pipeline itself lives in `tools.run_cbmc`; this module adds the command-line interface,
the checks that a passing function has a contract and that the run is not vacuous
(`tools.util.vacuity`), the verification-attempt log, and mutation testing (only when the function
verifies, and not vacuously).
"""

import argparse
import json
import sys
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger

from tools.run_cbmc import RunCbmcResult, has_contract, run_cbmc
from tools.util.mutation import (
    MutationScore,
    MutationTestingResult,
    generate_mutants_and_compute_score,
    get_mutation_testing_results_for_client,
)
from tools.util.vacuity import Vacuity, check_vacuity, describe_vacuous_result

# Per-subprocess CBMC timeout for each *mutant* when this tool reports mutation feedback to the
# agent. The function itself is still verified under the full `tools.run_cbmc` timeout, and the
# evaluation metric (`eval/mutants/evaluate_specification_quality.py`) always scores mutants under
# that full timeout; this smaller budget only bounds how long the agent waits for feedback. In
# practice almost every mutant is decided within a few seconds, while the rare hard mutant would
# otherwise stall the session for the full timeout only to be reported as undecided anyway. A
# mutant that exceeds this budget is reported as timed out -- never as killed.
_AGENT_MUTANT_TIMEOUT_SEC = 120

# Exit status of the tool when the pipeline passes but that does not count as verified: the proof
# is vacuous, or the function has no contract.
_EXIT_NOT_VERIFIED = 1

# Suffix of the sibling JSONL log in which `main` records one entry per top-level verification
# attempt (i.e., per `avocado-run-cbmc` invocation). Public because `avocado-verify` reads this
# log to count how many times the agent attempted to verify a function. Keep the two in sync.
VERIFICATION_ATTEMPTS_LOG_SUFFIX = "-verification-attempts.jsonl"

# Suffix of the sibling JSONL log in which `main` records the mutation-testing result of each
# passing verification attempt. It is a separate log because the attempt itself is recorded before
# mutation testing starts: a passing attempt with no record here is one whose mutation testing
# never finished (the session was stopped while it ran). Public because `avocado-verify` copies
# the log out of the session's private copy.
MUTATION_FEEDBACK_LOG_SUFFIX = "-mutation-feedback.jsonl"


def main() -> None:
    """Run CBMC on a function."""
    # The description omits the unwinding and depth bounds, to avoid inviting the agent to hard-code
    # them into specifications (which AGENTS.md forbids). This does not hide the bounds from the
    # agent: docs/running-cbmc-directly.md states them, because its commands must be runnable.
    parser = argparse.ArgumentParser(
        description=(
            "Run CBMC on a function, with bounded loop unwinding and bounded depth. "
            "Exits with status 0 on verification success. A run that passes is checked for "
            "vacuity with one more CBMC run; a vacuous pass is reported as VACUOUS and exits "
            "with a non-zero status. "
            "On success, additionally runs mutation testing, which re-runs the full CBMC "
            "pipeline once per mutant (several mutants in parallel, with a timeout of "
            f"{_AGENT_MUTANT_TIMEOUT_SEC} s on each CBMC step); this can take a few minutes, "
            "during which the tool is mostly silent. This is expected, not a hang -- do not "
            "interrupt the process. Consider running it in the background and polling for "
            "completion."
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

    result = verify_function(args.function, args.file, include_dirs=args.include_dirs)
    # Record this top-level verification attempt (pass or fail) so `avocado-verify` can gate its
    # per-function loop on the agent having actually attempted verification often enough.
    _log_verification_attempt(args.file, args.function, result)
    if result.is_function_verified:
        # The function verified, so run mutation testing to assess the strength of its
        # specification and report the kill score (plus any surviving mutants) to the client.
        started = time.monotonic()
        score = generate_mutants_and_compute_score(
            file_path=args.file,
            target_function=args.function,
            include_dirs=args.include_dirs,
            skip_reverification=True,
            mutant_timeout_sec=_AGENT_MUTANT_TIMEOUT_SEC,
        )
        _log_mutation_feedback(args.file, args.function, score, seconds=time.monotonic() - started)
        print(result.response)
        print(get_mutation_testing_results_for_client(score))
        sys.exit(0)
    else:
        print(result.response)
        # A vacuous or contract-less pass carries the pipeline's own exit code, which is 0.
        sys.exit(result.returncode or _EXIT_NOT_VERIFIED)


def verify_function(
    function: str,
    file_path: str,
    include_dirs: list[str] | None = None,
    *,
    vacuity_check: bool = True,
) -> RunCbmcResult:
    """Run the CBMC pipeline on `function`, then check a passing run for a contract and vacuity.

    A run that passes is then checked for a contract: a function without one is checked against
    an empty contract, which nothing can violate, so it is returned with
    `RunCbmcResult.no_contract` set (printing as `NO_CONTRACT`) and does not count as verified.
    A run that passes and has a contract is then checked for vacuity
    (`tools.util.vacuity.check_vacuity`), with one more run of the pipeline. A vacuous pass is
    returned with `RunCbmcResult.vacuity_check_result` set, so it does not count as verified
    (`is_function_verified` is False and it prints as `VACUOUS`), and with a response that says
    what was found. This is the one function through which the agent's tool, the harness's ground
    truth and the harness's re-verification of other functions verify a function, so they all
    agree on what a contract-less or vacuous proof is. When the check itself gives no answer the pass stands,
    with a `vacuity_check_result` whose vacuity is `Vacuity.UNKNOWN`.

    Args:
        function (str): Name of the function to verify.
        file_path (str): Path to the C file defining the function.
        include_dirs (list[str] | None): Directories forwarded to `goto-cc` as `-I` flags.
        vacuity_check (bool): When False, a passing run is returned as is, unchecked.

    Returns:
        RunCbmcResult: The outcome of the run.
    """
    result = run_cbmc(
        function_to_verify=function,
        file_containing_function_to_verify=file_path,
        include_dirs=include_dirs,
    )
    if not result.is_function_verified:
        return result
    if has_contract(function, file_path, include_dirs) is False:
        return replace(
            result,
            no_contract=True,
            response=(
                f"{function} has no contract, which does not count as verified: with no "
                "__CPROVER_requires, __CPROVER_ensures or __CPROVER_assigns clause on the "
                "definition that is compiled, CBMC checks an empty contract that nothing can "
                "violate. Write the function's contract between its signature and its body."
            ),
        )
    if not vacuity_check:
        return result
    vacuity_check_result = check_vacuity(function, file_path, include_dirs, variant=result.variant)
    vacuity_status = vacuity_check_result.vacuity
    if vacuity_status is Vacuity.UNKNOWN:
        logger.warning(
            f"{function}: vacuity could not be checked ({vacuity_check_result.message}); "
            "the pass stands"
        )
        return replace(result, vacuity_check_result=vacuity_check_result)
    if vacuity_status is Vacuity.DEPTH_LIMIT_REACHED:
        logger.warning(
            f"{function}: no execution enters the body within the depth bound, although the "
            "preconditions can hold; the pass stands"
        )
        return replace(result, vacuity_check_result=vacuity_check_result)
    if vacuity_status is Vacuity.NOT_VACUOUS:
        return replace(result, vacuity_check_result=vacuity_check_result)
    return replace(
        result,
        vacuity_check_result=vacuity_check_result,
        response=describe_vacuous_result(function, vacuity_check_result),
    )


def _log_verification_attempt(
    file_under_verification: str, function: str, result: RunCbmcResult
) -> None:
    """Append a record of one top-level verification attempt to a sibling JSONL log.

    Exactly one record is written per `avocado-run-cbmc` invocation, regardless of whether
    verification succeeded, so a consumer can count how many times verification was *attempted*
    for a given function (not how many times it passed). `avocado-verify` reads this log to gate
    its per-function loop. Failures to write are swallowed so attempt logging never breaks a run.

    Args:
        file_under_verification (str): The file that contains the function under verification.
        function (str): The function whose verification was attempted.
        result (RunCbmcResult): The outcome of the verification attempt.
    """
    record = {
        "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "function": function,
        "file": file_under_verification,
        "verified": result.is_function_verified,
        "verdict": str(result),
        "vacuity": None
        if result.vacuity_check_result is None
        else str(result.vacuity_check_result.vacuity),
    }
    _append_to_sibling_log(file_under_verification, VERIFICATION_ATTEMPTS_LOG_SUFFIX, record)


def _log_mutation_feedback(
    file_under_verification: str, function: str, result: MutationTestingResult, *, seconds: float
) -> None:
    """Append the mutation-testing result of one passing verification attempt to a sibling log.

    One record is written per passing `avocado-run-cbmc` invocation whose mutation testing ran to
    completion, in the order of the attempts, so the kill scores of successive attempts on a
    function can be compared. Failures to write are swallowed, as for the attempts log.

    Args:
        file_under_verification (str): The file that contains the function under verification.
        function (str): The function that verified.
        result (MutationTestingResult): The outcome of mutation testing.
        seconds (float): How long mutation testing took.
    """
    record: dict[str, str | int | float | bool | None] = {
        "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "function": function,
        "file": file_under_verification,
        "seconds": round(seconds, 1),
    }
    if isinstance(result, MutationScore):
        record |= {
            "was_mutation_tested": True,
            "kill_score": result.kill_score,
            "total": result.num_mutants,
            "killed": result.num_killed,
            "survived": result.num_survived,
            "timed_out": result.num_timed_out,
            "compile_failed": result.num_compile_failed,
            "instrumentation_failed": result.num_instrumentation_failed,
        }
    else:
        # No mutants could be generated, so there is no score to raise.
        record |= {"was_mutation_tested": False, "kill_score": None, "reason": str(result)}
    _append_to_sibling_log(file_under_verification, MUTATION_FEEDBACK_LOG_SUFFIX, record)


def _append_to_sibling_log(file_under_verification: str, suffix: str, record: dict) -> None:
    """Append one record to the JSONL log `<stem><suffix>` beside the file under verification.

    Args:
        file_under_verification (str): The file that contains the function under verification.
        suffix (str): The log's suffix, appended to the file's stem.
        record (dict): The JSON-serializable record to write.
    """
    source_path = Path(file_under_verification)
    log_path = source_path.with_name(f"{source_path.stem}{suffix}")
    try:
        with log_path.open("a") as f:
            f.write(json.dumps(record) + "\n")
    except OSError:
        # Best-effort: never let logging stop the tool from making progress.
        pass


if __name__ == "__main__":
    main()
