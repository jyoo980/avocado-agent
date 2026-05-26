"""Run mutation testing on a function w.r.t. its specification and report the score.

Usage:
    % avocado-get-mutation-score --function <FUNCTION_NAME> \
                       --file <PATH_TO_C_FILE> \
                       [-I <PATH_TO_INCLUDE_DIR(S)>]...
"""

import argparse
import json
import sys

from tools.util.mutation import (
    MutantVerificationResult,
    MutationScore,
    generate_mutants_and_compute_score,
)


def main() -> None:
    """Run mutation testing on a function w.r.t. its specification and report the score."""
    parser = argparse.ArgumentParser(
        description=(
            "Run mutation testing on a function w.r.t. its specification and report the score."
        )
    )
    parser.add_argument(
        "--function",
        required=True,
        help="Name of the function to run mutation testing on.",
    )
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

    score = generate_mutants_and_compute_score(
        file_path=args.file,
        target_function=args.function,
        include_dirs=args.include_dirs,
    )
    if score is None:
        print(
            f"{args.function} did not verify; cannot score mutants",
            file=sys.stderr,
        )
        sys.exit(1)

    print(json.dumps(_get_mutation_score_summary_with_surviving_mutant_diffs(score)))
    sys.exit(0)


def _get_mutation_score_summary_with_surviving_mutant_diffs(
    mutation_score: MutationScore,
) -> dict[str, str | int | float | list[str]]:
    """Return the mutation score summary with diffs for surviving mutants.

    Args:
        mutation_score (MutationScore): The mutation score for a function.

    Returns:
        dict[str, str | int | float | list[str]]: The mutation score summary with diffs for
            surviving mutants.
    """
    surviving_mutant_diffs = [
        mutant_vresult.mutant.get_unified_diff()
        for mutant_vresult in mutation_score.results
        if not mutant_vresult.killed and _is_valid_mutation_vresult(mutant_vresult)
    ]
    return mutation_score.summary() | {"surviving_mutant_diffs": surviving_mutant_diffs}


def _is_valid_mutation_vresult(mutant_vresult: MutantVerificationResult) -> bool:
    """Return True iff the given mutant vresult has not timed out and has successfully compiled.

    Args:
        mutant_vresult (MutantVerificationResult): The MutationVerificationResult to check for
            validity.

    Returns:
        bool: True iff the given mutant vresult has not timed out and has successfully compiled.
    """
    return not mutant_vresult.compile_failed and not mutant_vresult.timed_out


if __name__ == "__main__":
    main()
