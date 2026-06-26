"""Return the mutant(s) for a given function.

Usage:
    % avocado-get-mutants --function <FUNCTION_NAME> --file <PATH_TO_C_FILE>
"""

import argparse
import sys

from eval.mutants.mutate_function import get_mutants


def main() -> None:
    """Return the mutant(s) for a given function."""
    parser = argparse.ArgumentParser(description=("Return the mutant(s) for a given function."))
    parser.add_argument(
        "--function",
        required=True,
        help="Name of the function for which to return mutants.",
    )
    parser.add_argument("--file", required=True, help="Path to the C file defining the function.")
    args = parser.parse_args()

    mutants = get_mutants(
        file_path=args.file,
        function_name=args.function,
    )

    if mutants:
        print("You must try to kill the follow mutant(s):\n")
        for mutant in mutants:
            print(mutant.get_unified_diff())
    else:
        print(f"No mutant(s) generated for '{args.function}' (no mutable operators)")

    sys.exit(0)


if __name__ == "__main__":
    main()
