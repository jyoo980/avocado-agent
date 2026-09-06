"""Run CBMC on a function and perform mutation testing.

This is the `avocado-run-cbmc` entry point that the inner agent calls. It wraps the shared pipeline
in `tools.run_cbmc` (goto-cc → goto-instrument → cbmc), records the attempt in a sibling JSONL log
that `avocado-verify` reads, and -- when the function verifies -- runs mutation testing and reports
the kill score plus any surviving mutants.

Usage:
    % avocado-run-cbmc --function <FUNCTION_NAME> \
                       --file <PATH_TO_C_FILE> \
                       [-I <PATH_TO_INCLUDE_DIR(S)>]...
"""

import argparse
import json
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger

from tools.run_cbmc import RunCbmcResult, run_cbmc
from tools.util.mutation import (
    generate_mutants_and_compute_score,
    get_mutation_testing_results_for_client,
)

# Per-subprocess CBMC timeout for each *mutant* when this tool reports mutation feedback to the
# agent. The function itself is still verified under the full `tools.run_cbmc` timeout, and the
# evaluation metric (`eval/mutants/evaluate_specification_quality.py`) always scores mutants under
# that full timeout; this smaller budget only bounds how long the agent waits for feedback. In
# practice almost every mutant is decided within a few seconds, while the rare hard mutant would
# otherwise stall the session for the full timeout only to be reported as undecided anyway. A
# mutant that exceeds this budget is reported as timed out -- never as killed.
_AGENT_MUTANT_TIMEOUT_SEC = 120

# Suffix of the sibling JSONL log in which `main` records one entry per top-level verification
# attempt (i.e., per `avocado-run-cbmc` invocation). Public because `avocado-verify` reads this
# log to count how many times the agent attempted to verify a function. Keep the two in sync.
VERIFICATION_ATTEMPTS_LOG_SUFFIX = "-verification-attempts.jsonl"


def main() -> None:
    """Run CBMC on a function."""
    parser = argparse.ArgumentParser(
        description=(
            "Run CBMC on a function with loop unwinding = _CBMC_UNWIND, depth = _CBMC_DEPTH. "
            "Exits with status 0 on verification success. "
            "On success, additionally runs mutation testing, which re-runs the full CBMC "
            f"pipeline once per mutant (concurrently, up to {_AGENT_MUTANT_TIMEOUT_SEC} s each); "
            "this can take a few minutes, during which the tool is mostly silent. This is "
            "expected, not a hang -- do not interrupt the process. Consider running it in the "
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

    result = verify_function(args.function, args.file, include_dirs=args.include_dirs)
    # Record this top-level verification attempt (pass or fail) so `avocado-verify` can gate its
    # per-function loop on the agent having actually attempted verification often enough.
    _log_verification_attempt(args.file, args.function, result)
    if result.is_function_verified:
        # The function verified, so run mutation testing to assess the strength of its
        # specification and report the kill score (plus any surviving mutants) to the client.
        score = generate_mutants_and_compute_score(
            file_path=args.file,
            target_function=args.function,
            include_dirs=args.include_dirs,
            skip_reverification=True,
            mutant_timeout_sec=_AGENT_MUTANT_TIMEOUT_SEC,
        )
        if score:
            print(result.response)
            print(get_mutation_testing_results_for_client(score))
        sys.exit(0)
    else:
        print(result.response)
        sys.exit(result.returncode)


def verify_function(
    function: str, file_path: str, include_dirs: list[str] | None = None
) -> RunCbmcResult:
    """Run the CBMC pipeline on `function` in a private scratch directory.

    The pipeline writes its `<function>.goto` / `checking-<function>-contracts.goto` intermediates
    relative to its working directory. Running it in a fresh temporary directory keeps those files
    out of the caller's working directory and lets several `avocado-run-cbmc` processes (e.g. agent
    sessions for different files that happen to share a function name) run concurrently without
    clobbering one another. Paths are resolved first because the pipeline's commands are executed
    relative to the scratch directory.

    Args:
        function (str): Name of the function to verify.
        file_path (str): Path to the C file defining the function.
        include_dirs (list[str] | None): Directories forwarded to `goto-cc` as `-I` flags.

    Returns:
        RunCbmcResult: The outcome of the run.
    """
    with tempfile.TemporaryDirectory(prefix="avocado-verify-") as scratch_dir:
        return run_cbmc(
            function_to_verify=function,
            file_containing_function_to_verify=str(Path(file_path).resolve()),
            include_dirs=[str(Path(d).resolve()) for d in include_dirs or []],
            cwd=scratch_dir,
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
    source_path = Path(file_under_verification)
    log_path = source_path.with_name(f"{source_path.stem}{VERIFICATION_ATTEMPTS_LOG_SUFFIX}")
    record = {
        "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "function": function,
        "file": file_under_verification,
        "verified": result.is_function_verified,
        "verdict": str(result),
    }
    try:
        with log_path.open("a") as f:
            f.write(json.dumps(record) + "\n")
    except OSError:
        # Best-effort: never let attempt logging stop the tool from making progress.
        pass


if __name__ == "__main__":
    main()
