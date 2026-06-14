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
    for raw_result in score.results:
        result = {
            "kind": "mutant_result",
            "file": score.file,
            "function": score.target_function,
            "operator_class": raw_result.mutant.operator_class,
            "original": raw_result.mutant.original_operator,
            "replacement": raw_result.mutant.replacement_operator,
            "line": raw_result.mutant.line,
            "column": raw_result.mutant.column,
            "killed": raw_result.killed,
            "timed_out": raw_result.timed_out,
            "returncode": raw_result.returncode,
        }
        if args.keep_artifacts:
            result |= {"path_to_mutant": raw_result.path_to_mutant}
        output_lines.append(json.dumps(result))

    body = "\n".join(output_lines) + "\n"
    if args.jsonl:
        output_path = Path(args.jsonl)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(body, encoding="utf-8")
    else:
        sys.stdout.write(body)


if __name__ == "__main__":
    sys.exit(main())
