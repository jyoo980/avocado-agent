"""Run CBMC on a function and, when it verifies, perform mutation testing.

This module is the `avocado-run-cbmc` entry point. The CBMC pipeline itself lives in
`tools.run_cbmc`, which is the single implementation; this module only adds the command-line
interface, the mutation-testing pass, and the per-invocation attempts log that `avocado-verify`
reads to decide whether a function deserves another agent session.

Usage:
    % avocado-run-cbmc --function <FUNCTION_NAME> \
                       --file <PATH_TO_C_FILE> \
                       [-I <PATH_TO_INCLUDE_DIR(S)>]... \
                       [--recheck-equivalent]
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger

# Re-exported so existing importers of this module keep working: the pipeline is defined once, in
# `tools.run_cbmc`.
from tools.run_cbmc import (
    CbmcStep,
    RunCbmcResult,
    compile_with_goto_cc,
    has_missing_body_for_callee_or_function_message,
    has_recursion_inlining_error_message,
    run_cbmc,
)
from tools.util.mutation import (
    MutationScore,
    generate_mutants_and_compute_score,
    get_mutation_testing_results_for_client,
)

__all__ = [
    "VERIFICATION_ATTEMPTS_LOG_SUFFIX",
    "CbmcStep",
    "RunCbmcResult",
    "compile_with_goto_cc",
    "has_missing_body_for_callee_or_function_message",
    "has_recursion_inlining_error_message",
    "main",
    "run_cbmc",
]

# Suffix of the sibling JSONL log in which `main` records one entry per top-level verification
# attempt (i.e., per `avocado-run-cbmc` invocation). Public because `avocado-verify` reads this
# log to gate its per-function loop. Keep the two in sync.
VERIFICATION_ATTEMPTS_LOG_SUFFIX = "-verification-attempts.jsonl"


def main() -> None:
    """Run CBMC on a function and report the result, with mutation testing on success."""
    parser = argparse.ArgumentParser(
        description=(
            "Run CBMC on a function. Exits with status 0 on verification success. "
            "On success, additionally runs mutation testing, which re-runs the CBMC pipeline "
            "once per mutant (concurrently, with a per-mutant timeout); mutants whose verdict "
            "cannot have changed since a previous run are replayed from a cache instead of being "
            "re-verified. This can still take several minutes, during which the tool is mostly "
            "silent. That is expected, not a hang -- do not interrupt the process."
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
    parser.add_argument(
        "--recheck-equivalent",
        action="store_true",
        help=(
            "Re-verify every mutant, ignoring remembered verdicts and equivalence judgements. "
            "Slower, but audits the heuristics that would otherwise skip mutants."
        ),
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
    if not result.is_function_verified:
        _log_verification_attempt(args.file, args.function, result)
        print(result.response)
        sys.exit(result.returncode)

    # The function verified, so run mutation testing to assess the strength of its specification
    # and report the kill score (plus any surviving mutants) to the client.
    score = generate_mutants_and_compute_score(
        file_path=args.file,
        target_function=args.function,
        include_dirs=args.include_dirs,
        skip_reverification=True,
        recheck_equivalent=args.recheck_equivalent,
    )
    # Logged after mutation testing so the attempt record carries the kill score: `avocado-verify`
    # gates further agent sessions on whether the score is still improving, which it cannot see
    # from the pass/fail verdict alone.
    _log_verification_attempt(args.file, args.function, result, score)
    print(result.response)
    print(get_mutation_testing_results_for_client(score))
    sys.exit(0)


def _log_verification_attempt(
    file_under_verification: str,
    function: str,
    result: RunCbmcResult,
    score: object | None = None,
) -> None:
    """Append a record of one top-level verification attempt to a sibling JSONL log.

    Exactly one record is written per `avocado-run-cbmc` invocation, regardless of whether
    verification succeeded, so a consumer can count how many times verification was *attempted*
    for a given function (not how many times it passed). When mutation testing ran, the record also
    carries the kill scores and bucket counts, which is what lets `avocado-verify` distinguish a
    session that strengthened the specification from one that spun in place. Failures to write are
    swallowed so attempt logging never breaks a run.

    Args:
        file_under_verification (str): The file that contains the function under verification.
        function (str): The function whose verification was attempted.
        result (RunCbmcResult): The outcome of the verification attempt.
        score (object | None): The mutation-testing result, when one was computed. Only a
            `MutationScore` contributes fields; other results (no mutants, baseline failure)
            leave the mutation fields absent.
    """
    source_path = Path(file_under_verification)
    log_path = source_path.with_name(f"{source_path.stem}{VERIFICATION_ATTEMPTS_LOG_SUFFIX}")
    record = {
        "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "function": function,
        "file": file_under_verification,
        "verified": result.is_function_verified,
        "verdict": str(result),
    }
    if isinstance(score, MutationScore):
        record["mutation"] = score.summary()
    try:
        with log_path.open("a") as f:
            f.write(json.dumps(record) + "\n")
    except OSError:
        # Best-effort: never let attempt logging stop the tool from making progress.
        pass


if __name__ == "__main__":
    main()
