"""Run mutation testing on a function w.r.t. its specification and report the score.

Usage:
    % avocado-get-mutation-score --function <FUNCTION_NAME> \
                       --file <PATH_TO_C_FILE> \
                       [-I <PATH_TO_INCLUDE_DIR(S)>]...
"""

import argparse
import sys

from tools.util.mutation import (
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

    print(get_mutation_testing_results_for_client(score))
    sys.exit(0)


if __name__ == "__main__":
    main()
