#!/usr/bin/env -S uv run --quiet python3

"""Top-level driver for specification quality metrics.

Walks one or more annotated C files and emits a JSONL stream of metric records.

Each metric is a separate flag so the user can specify the ones they want, namely:
    - --mutation: Mutation scores for specifications that can "kill" a mutant function.
    - --redundancy: Clause redundancy.

Mutation scoring of different functions is independent, so with `--jobs N` (default: the CPU
count) up to N functions are scored concurrently, each in its own thread; the mutants of every
function additionally fan out in their own thread pool. Machine-wide load is bounded by the
subprocess cap in `tools.run_cbmc` (`AVOCADO_MAX_CONCURRENT_CBMC`). Records are still emitted in
the sequential order (files sorted, functions sorted within each file), so the JSONL output is
identical to a `--jobs 1` run. Clause-redundancy scoring is not parallelized: its scratch files are
named per source file, not per function, so it runs sequentially after mutation scoring.

Usage:
    % ./eval/mutants/evaluate_specification_quality.py <PATH_TO_C_FILE_OR_DIR> \
            [--auto-include] \
            [--include-dirs] \
            [--mutation] \
            [--redundancy] \
            [--keep-artifacts]
            [--jobs N] \
            [--jsonl PATH] \
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval.mutants.compute_clause_redundancy import compute_clause_redundancy_score
from eval.mutants.generate_mutants_and_compute_score import generate_mutants_and_compute_score
from eval.mutants.util import get_files_with_extension
from tools.util import get_functions_with_cprover_annotations
from tools.util.mutation import MutationScore


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
        "--include-dirs", action="append", help="Path(s) to stubs to use in verification."
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
    parser.add_argument(
        "--jobs",
        type=int,
        default=os.cpu_count() or 1,
        metavar="N",
        help=(
            "Score up to N functions concurrently (mutation metric only). Defaults to the CPU "
            "count; use 1 for a fully sequential run."
        ),
    )
    parser.add_argument("--jsonl", default=None, help="Write records to this JSONL file.")
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be at least 1.")

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
        Path(args.jsonl).open("w", encoding="utf-8") if args.jsonl else sys.stdout  # ruff: ignore[open-file-with-context-handler]
    )
    try:
        include_dirs_from_cli = args.include_dirs or []
        with ThreadPoolExecutor(max_workers=args.jobs) as executor:
            # Submit every file's mutation-scoring tasks up front so the pool is never idle while
            # one file's stragglers finish, then drain results file by file in the original order.
            submitted = [
                _submit_file(
                    source=source,
                    auto_include=args.auto_include,
                    include_dirs=include_dirs_from_cli,
                    run_mutation=args.mutation,
                    keep_artifacts=args.keep_artifacts,
                    executor=executor,
                )
                for source in input_files
            ]
            for submitted_file in submitted:
                _write_file_records(
                    submitted_file,
                    out=output_stream,
                    run_redundancy=args.redundancy,
                    keep_artifacts=args.keep_artifacts,
                )
    finally:
        if args.jsonl:
            output_stream.close()
    return sys.exit(0)


@dataclass(frozen=True)
class _SubmittedFile:
    """The in-flight mutation-scoring work for one source file.

    Attributes:
        source (Path): The path to the file under evaluation.
        functions (list[str]): The file's annotated functions, sorted.
        mutation_futures (list[Future[dict]]): One pending `mutation_summary` record per function,
            in `functions` order; empty when mutation scoring is disabled.
    """

    source: Path
    functions: list[str]
    mutation_futures: list[Future[dict]]


def _submit_file(
    source: Path,
    auto_include: bool,
    include_dirs: list[str],
    run_mutation: bool,
    keep_artifacts: bool,
    executor: ThreadPoolExecutor,
) -> _SubmittedFile:
    """Submit mutation scoring for every annotated function in `source` to `executor`.

    Arguments:
        source (Path): The path to the file for evaluation.
        auto_include (bool): True iff an `include` dir (i.e., a dir containing headers) should be
            automatically detected.
        include_dirs (list[str]): List of directories containing files (e.g., headers) that should
            be included in verification.
        run_mutation (bool): True iff mutation testing should be reported.
        keep_artifacts (bool): True iff the mutant files should be retained after evaluation.
        executor (ThreadPoolExecutor): Pool in which per-function mutation scoring runs.

    Returns:
        _SubmittedFile: The file's functions and their pending mutation records.
    """
    logger.info(f"Processing {source}")
    functions_with_cprover_annotations = sorted(get_functions_with_cprover_annotations(str(source)))
    if not functions_with_cprover_annotations:
        logger.warning(f"{source} had no functions with CBMC annotations")
        return _SubmittedFile(source, [], [])

    include_dirs = (
        [*_autodetect_include_dirs(str(source)), *include_dirs] if auto_include else include_dirs
    )
    if include_dirs:
        logger.debug(f"[auto-include] using {include_dirs}")

    futures: list[Future[dict]] = []
    if run_mutation:
        futures = [
            executor.submit(
                _mutation_record,
                source,
                function,
                include_dirs=include_dirs,
                keep_artifacts=keep_artifacts,
            )
            for function in functions_with_cprover_annotations
        ]
    return _SubmittedFile(source, functions_with_cprover_annotations, futures)


def _write_file_records(
    submitted: _SubmittedFile, out: IO[str], run_redundancy: bool, keep_artifacts: bool
) -> None:
    """Write one file's metric records to `out`: mutation summaries, then redundancy scores.

    Mutation records are written in function order as their futures complete; redundancy scoring
    (when enabled) then runs sequentially in the calling thread, because its scratch files are
    named per source file rather than per function.

    Arguments:
        submitted (_SubmittedFile): The file's pending mutation work.
        out (IO[str]): The output.
        run_redundancy (bool): True iff redundancy scoring should be reported.
        keep_artifacts (bool): True iff the mutant files should be retained after evaluation.
    """
    for future in submitted.mutation_futures:
        out.write(json.dumps(future.result()) + "\n")
    out.flush()
    if not run_redundancy:
        return
    for function in submitted.functions:
        if clause_redundancy_score := compute_clause_redundancy_score(
            str(submitted.source), function, keep_artifacts=keep_artifacts
        ):
            out.write(json.dumps(clause_redundancy_score.summary()) + "\n")
        else:
            logger.warning(
                f"No redundancy score calculation reported for: '{submitted.source!s}#{function}'"
            )


def _mutation_record(
    source: Path, function: str, *, include_dirs: list[str], keep_artifacts: bool
) -> dict:
    """Return the mutation-summary JSONL record for one function.

    Arguments:
        source (Path): The path to the file containing the function.
        function (str): The function to mutation test.
        include_dirs (list[str]): Directories forwarded to CBMC as include paths.
        keep_artifacts (bool): True iff the mutant files should be retained after evaluation.

    Returns:
        dict: The `mutation_summary` record: the `MutationScore` summary when the function was
            mutation tested, else a record whose `metadata` explains why it was not.
    """
    mutation_testing_result = generate_mutants_and_compute_score(
        str(source), function, keep_artifacts=keep_artifacts, include_dirs=include_dirs
    )
    if isinstance(mutation_testing_result, MutationScore):
        return mutation_testing_result.summary()
    return {
        "kind": "mutation_summary",
        "was_mutation_tested": False,
        "file": str(source.resolve()),
        "function": function,
        "metadata": str(mutation_testing_result),
    }


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
