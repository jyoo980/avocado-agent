"""Run mutation testing on a function w.r.t. its specification and report the score.

Usage:
    % avocado-get-mutation-score --function <FUNCTION_NAME> \
                       --file <PATH_TO_C_FILE> \
                       [-I <PATH_TO_INCLUDE_DIR(S)>]...
"""

import argparse
import sys

from tools.util.mutation import (
    MutationScore,
    generate_mutants_and_compute_score,
    get_mutation_testing_results_for_client,
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
    parser.add_argument(
        "--recheck-equivalent",
        action="store_true",
        help=(
            "Re-verify every mutant, ignoring remembered verdicts and equivalence judgements. "
            "Slower, but audits the heuristics that would otherwise skip mutants."
        ),
    )
    args = parser.parse_args()

    mutation_testing_result = generate_mutants_and_compute_score(
        file_path=args.file,
        target_function=args.function,
        include_dirs=args.include_dirs,
        recheck_equivalent=args.recheck_equivalent,
    )

    if isinstance(mutation_testing_result, MutationScore):
        print(get_mutation_testing_results_for_client(mutation_testing_result))
        sys.exit(0)

    print(mutation_testing_result)
    sys.exit(1)


if __name__ == "__main__":
    main()
