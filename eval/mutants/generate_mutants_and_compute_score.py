#!/usr/bin/env -S uv run --quiet python3

"""CLI shim around `tools.util.mutation.generate_mutants_and_compute_score`.

Compute the mutation score for a function with CBMC annotations.

Usage:
    % ./eval/mutants/generate_mutants_and_compute_score.py \
        --function <NAME> \
        --file <PATH_TO_C_FILE> \
        [--keep-artifacts] \
        [--jsonl PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tools.util.mutation import (
    MutantVerificationResult,
    MutationScore,
    generate_mutants_and_compute_score,
)

__all__ = [
    "MutantVerificationResult",
    "MutationScore",
    "generate_mutants_and_compute_score",
]


def main() -> None:
    """Compute the mutation score for a function with CBMC annotations."""
    parser = argparse.ArgumentParser(
        description="Run CBMC against body mutants of a function and report kill rate."
    )
    parser.add_argument("--function", required=True, help="Function for which to generate mutants.")
    parser.add_argument("--file", required=True, help="Path to the C file containing the function.")
    parser.add_argument(
        "--jsonl",
        default=None,
        help="Write per-mutant + summary JSONL records to this path (default: stdout).",
    )
    parser.add_argument(
        "--keep-artifacts",
        action="store_true",
        help="Keep mutant .c files for inspection. By default they are removed after scoring.",
    )
    args = parser.parse_args()

    score = generate_mutants_and_compute_score(
        file_path=args.file,
        target_function=args.function,
        keep_artifacts=args.keep_artifacts,
    )

    if not score:
        # The function does not verify in the first place.
        sys.exit(1)

    output_lines: list[str] = [json.dumps(score.summary())]
    output_lines.extend(
        json.dumps(
            {
                "kind": "mutant_result",
                "file": score.file,
                "function": score.function,
                "operator_class": result.mutant.operator_class,
                "original": result.mutant.original_operator,
                "replacement": result.mutant.replacement_operator,
                "line": result.mutant.line,
                "column": result.mutant.column,
                "killed": result.killed,
                "timed_out": result.timed_out,
                "returncode": result.returncode,
            }
        )
        for result in score.results
    )

    body = "\n".join(output_lines) + "\n"
    if args.jsonl:
        output_path = Path(args.jsonl)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(body, encoding="utf-8")
    else:
        sys.stdout.write(body)


if __name__ == "__main__":
    sys.exit(main())
