"""Run CBMC on a function and check that a passing run is not vacuous.

Usage:
    % avocado-run-cbmc --function <FUNCTION_NAME> \
                       --file <PATH_TO_C_FILE> \
                       [-I <PATH_TO_INCLUDE_DIR(S)>]...

The CBMC pipeline itself, and the check that a passing function has a contract, live in
`tools.run_cbmc`; this module adds the command-line interface, the check that the run is not
vacuous (`tools.util.vacuity`), and the verification-attempt log.
"""

import argparse
import json
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger

from tools.run_cbmc import RunCbmcResult
from tools.run_cbmc import verify_function as verify_function_and_contract
from tools.util.vacuity import Vacuity, check_vacuity, describe_vacuous_result

# Exit status of the tool when the pipeline passes but that does not count as verified: the proof
# is vacuous, the function has no contract, or its contract check failed.
_EXIT_NOT_VERIFIED = 1

# Suffix of the sibling JSONL log in which `main` records one entry per top-level verification
# attempt (i.e., per `avocado-run-cbmc` invocation). Public because `avocado-verify` reads this
# log to count how many times the agent attempted to verify a function. Keep the two in sync.
VERIFICATION_ATTEMPTS_LOG_SUFFIX = "-verification-attempts.jsonl"


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
            "with a non-zero status."
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

    result = verify_function(args.function, args.file, include_dirs=args.include_dirs)
    # Record this top-level verification attempt (pass or fail) so `avocado-verify` can gate its
    # per-function loop on the agent having actually attempted verification often enough.
    _log_verification_attempt(args.file, args.function, result)
    print(result.response)
    if result.is_function_verified:
        sys.exit(0)
    # A vacuous or contract-less pass, or one whose contract check failed, carries the pipeline's
    # own exit code, which is 0.
    sys.exit(result.returncode or _EXIT_NOT_VERIFIED)


def verify_function(
    function: str,
    file_path: str,
    include_dirs: list[str] | None = None,
    *,
    vacuity_check: bool = True,
) -> RunCbmcResult:
    """Run `tools.run_cbmc.verify_function` on `function`, then check a passing run for vacuity.

    `tools.run_cbmc.verify_function` runs the pipeline and checks that a passing function has a
    contract. A run that passes and has a contract is then checked for vacuity
    (`tools.util.vacuity.check_vacuity`), with one more run of the pipeline. A vacuous pass is
    returned with `RunCbmcResult.vacuity_check_result` set, so it does not count as verified
    (`is_function_verified` is False and it prints as `VACUOUS`), and with a response that says
    what was found. When the check itself gives no answer the pass stands, with a
    `vacuity_check_result` whose vacuity is `Vacuity.UNKNOWN`.

    Args:
        function (str): Name of the function to verify.
        file_path (str): Path to the C file defining the function.
        include_dirs (list[str] | None): Directories forwarded to `goto-cc` as `-I` flags.
        vacuity_check (bool): When False, a passing run is returned as is, unchecked.

    Returns:
        RunCbmcResult: The outcome of the run.
    """
    result = verify_function_and_contract(function, file_path, include_dirs)
    if not result.is_function_verified:
        return result
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
    source_path = Path(file_under_verification)
    log_path = source_path.with_name(f"{source_path.stem}{VERIFICATION_ATTEMPTS_LOG_SUFFIX}")
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
    try:
        with log_path.open("a") as f:
            f.write(json.dumps(record) + "\n")
    except OSError:
        # Best-effort: never let attempt logging stop the tool from making progress.
        pass


if __name__ == "__main__":
    main()
