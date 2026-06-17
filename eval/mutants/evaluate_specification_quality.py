#!/usr/bin/env -S uv run --quiet python3

"""Top-level driver for specification quality metrics.

Walks one or more annotated C files and emits a JSONL stream of metric records.

Each metric is a separate flag so the user can specify the ones they want, namely:
    - --mutation: Mutation scores for specifications that can "kill" a mutant function.
    - --redundancy: Clause redundancy.

Usage:
    % ./eval/mutants/evaluate_specification_quality.py <PATH_TO_C_FILE_OR_DIR> \
            [--auto-include] \
            [--mutation] \
            [--redundancy] \
            [--keep-artifacts]
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
        "--auto-include",
        help=(
            "For each .c file, look for a sibling include/ directory at "
            "<source>/../include and pass it to CBMC as an include path. "
            "Fits projects whose headers live next to their src/ tree."
        ),
        action="store_true",
    )
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
    parser.add_argument(
        "--keep-artifacts",
        action="store_true",
        help="Retain mutant .c files after evaluation. Defaults to False.",
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
                auto_include=args.auto_include,
                run_mutation=args.mutation,
                run_redundancy=args.redundancy,
                keep_artifacts=args.keep_artifacts,
            )
    finally:
        if args.jsonl:
            output_stream.close()
    return sys.exit(0)


def _process_file(
    source: Path,
    out: IO[str],
    auto_include: bool,
    run_mutation: bool,
    run_redundancy: bool,
    keep_artifacts: bool,
) -> None:
    """Run all enabled metrics for a single source file and write JSONL records to `out`.

    Arguments:
        source (Path): The path to the file for evaluation.
        out (IO[str]): The output.
        auto_include (bool): True iff an `include` dir (i.e., a dir containing headers) should be
            automatically detected.
        run_mutation (bool): True iff mutation testing should be reported.
        run_redundancy (bool): True iff redundancy scoring should be reported.
        keep_artifacts (bool): True iff the mutant files should be retained after evaluation.
    """
    logger.info(f"Processing {source}")
    functions_with_cprover_annotations = sorted(get_functions_with_cprover_annotations(str(source)))

    if not functions_with_cprover_annotations:
        logger.warning(f"{source} had no functions with CBMC annotations")
        return

    include_dirs = _autodetect_include_dirs(str(source)) if auto_include else []
    if include_dirs:
        logger.debug(f"[auto-include] using {include_dirs}")

    for function in functions_with_cprover_annotations:
        if run_mutation:
            if mutation_score := generate_mutants_and_compute_score(
                str(source),
                function,
                keep_artifacts=keep_artifacts,
                include_dirs=include_dirs,
            ):
                out.write(json.dumps(mutation_score.summary()) + "\n")

        if run_redundancy:
            if clause_redundancy_score := compute_clause_redundancy_score(
                str(source), function, keep_artifacts=keep_artifacts
            ):
                out.write(json.dumps(clause_redundancy_score.summary()) + "\n")
            else:
                logger.warning(
                    f"No redundancy score calculation reported for: '{source!s}#{function}'"
                )


def _autodetect_include_dirs(source_file: str) -> list[str]:
    """Return `[<source>/../include]` if that directory exists, else an empty list.

    Many CMake projects keep public headers in `<project>/include/` while sources live in
    `<project>/src/`. When that layout holds, returning the sibling `include/` directory lets
    CBMC resolve `#include "foo.h"` without the caller having to configure paths by hand.

    Args:
        source_file (str): Path to a `.c` file.

    Returns:
        list[str]: `[<resolved include dir>]` when present, else `[]`.
    """
    candidate = Path(source_file).resolve().parent.parent / "include"
    return [str(candidate)] if candidate.is_dir() else []


if __name__ == "__main__":
    sys.exit(main())
