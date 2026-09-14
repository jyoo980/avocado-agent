"""Record that a surviving mutant is equivalent to the original, so it stops being re-verified.

Some mutants cannot be killed by any specification: the operator swap produces a program that
behaves identically to the original, so no postcondition can distinguish them. Re-verifying such a
mutant on every run costs a full CBMC pipeline and can never change the kill score. Until now an
agent that worked this out had no way to say so, and the harness had no way to hear it.

This tool is that channel. A declaration is recorded with the justification that motivated it, so
it stays auditable rather than becoming a silent way to inflate a score, and
`avocado-run-cbmc --recheck-equivalent` re-tests every declared mutant when a run needs
unskewed numbers.

Usage:
    % avocado-mark-equivalent --function <FUNCTION_NAME> \
                              --file <PATH_TO_C_FILE> \
                              --mutant <MUTANT_ID> \
                              --reason "<why no specification can kill it>"
"""

import argparse
import sys

from tools.util.mutation_cache import MutationCache, compute_body_digest


def main() -> None:
    """Mark a mutant as equivalent to the original in the mutation cache."""
    parser = argparse.ArgumentParser(
        description=(
            "Record that a surviving mutant is semantically equivalent to the original, so future "
            "mutation-testing runs skip it instead of re-verifying it. Mutant ids are shown "
            "alongside each surviving mutant in `avocado-run-cbmc` output."
        )
    )
    parser.add_argument("--function", required=True, help="Name of the function to verify.")
    parser.add_argument("--file", required=True, help="Path to the C file defining the function.")
    parser.add_argument(
        "--mutant",
        required=True,
        help="The mutant's id, as reported in the surviving-mutant listing.",
    )
    parser.add_argument(
        "--reason",
        required=True,
        help="Why no specification could kill this mutant. Recorded for auditing.",
    )
    args = parser.parse_args()

    if not args.reason.strip():
        print("--reason must not be empty; explain why no specification could kill this mutant.")
        sys.exit(2)

    cache = MutationCache.load(args.file)
    # Load the function's entries first so a stale cache (the C body changed since the mutant was
    # recorded) is discarded rather than annotated with a declaration that no longer applies.
    body_digest = compute_body_digest(args.file, args.function)
    known = cache.entries_for(args.function, body_digest)

    if not cache.declare_equivalent(args.function, args.mutant, reason=args.reason.strip()):
        print(
            f"No mutant '{args.mutant}' is recorded for '{args.function}' in {args.file}. "
            "Run `avocado-run-cbmc` on the function first, then use an id from its "
            "surviving-mutant listing."
        )
        if known:
            print(f"Known mutant id(s) for {args.function}: {', '.join(sorted(known))}")
        sys.exit(1)

    cache.save()
    print(
        f"Recorded mutant {args.mutant} of '{args.function}' as equivalent; it will be skipped in "
        f"future mutation-testing runs and excluded from the adjusted kill score. "
        f"Cache: {cache.path}"
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
