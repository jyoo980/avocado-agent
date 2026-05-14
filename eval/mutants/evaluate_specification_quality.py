#!/usr/bin/env -S uv run --quiet python3

"""Top-level driver for specification quality metrics.

Walks one or more annotated C files and emits a JSONL stream of metric records.

Each metric is a separate flag so the user can specify the ones they want, namely:
    - --mutation: Mutation scores for specifications that can "kill" a mutant function.
    - --redundancy: Clause redundancy.

Usage:
    % ./eval/mutants/evaluate_specification_quality.py <PATH_TO_C_FILE_OR_DIR> \
            [--mutation] \
            [--redundancy] \
            [--jsonl PATH] \
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import IO

from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval.mutants.compute_clause_redundancy import compute_clause_redundancy_score
from eval.mutants.generate_mutants_and_compute_score import generate_mutants_and_compute_score
from eval.mutants.util import get_files_with_extension
from tools.util import get_functions_with_cprover_annotations


def main() -> None:
    """CLI entry point: orchestrate the spec-quality metric suite over a path."""
    parser = argparse.ArgumentParser(
        description="Run spec-quality metrics over annotated C functions."
    )
    parser.add_argument("path", help="Path to a C file or directory of C files.")
    parser.add_argument(
        "--mutation",
        action="store_true",
        help="Run mutation testing on mutated C functions.",
    )
    parser.add_argument(
        "--redundancy",
        action="store_true",
        help="Run redundant-clause calculations.",
    )
    parser.add_argument("--jsonl", default=None, help="Write records to this JSONL file.")
    args = parser.parse_args()

    logger.remove()
    logger.add(sys.stderr, level="INFO")

    if not (args.mutation or args.redundancy):
        parser.error("At least one of --mutation or --redundancy is required.")
        return 1

    input_files = get_files_with_extension(args.path, ".c")
    if not input_files:
        logger.error(f"No .c files found at: {args.path}")
        sys.exit(1)

    # This stream is closed in a `finally` block.
    output_stream: IO[str] = (
        Path(args.jsonl).open("w", encoding="utf-8") if args.jsonl else sys.stdout  # noqa: SIM115
    )
    try:
        for source in input_files:
            _process_file(
                source=source,
                out=output_stream,
                run_mutation=args.mutation,
                run_redundancy=args.redundancy,
            )
    finally:
        if args.jsonl:
            output_stream.close()
    return sys.exit(0)


def _process_file(
    source: Path,
    out: IO[str],
    run_mutation: bool,
    run_redundancy: bool,
) -> None:
    """Run all enabled metrics for a single source file and write JSONL records to `out`.

    Arguments:
        source (Path): The path to the file for evaluation.
        out (IO[str]): The output.
        run_mutation (bool): True iff mutation testing should be reported.
        run_redundancy (bool): True iff redundancy scoring should be reported.
    """
    logger.info(f"Processing {source}")
    annotated = sorted(get_functions_with_cprover_annotations(str(source)))

    for function in annotated:
        if run_mutation:
            if mutation_score := generate_mutants_and_compute_score(str(source), function):
                out.write(json.dumps(mutation_score.summary()) + "\n")
            else:
                logger.warning(f"No mutation testing score reported for: '{source!s}#{function}'")

        if run_redundancy:
            if clause_redundancy_score := compute_clause_redundancy_score(str(source), function):
                out.write(json.dumps(clause_redundancy_score.summary()) + "\n")
            else:
                logger.warning(
                    f"No redundancy score calculation reported for: '{source!s}#{function}'"
                )


if __name__ == "__main__":
    sys.exit(main())
