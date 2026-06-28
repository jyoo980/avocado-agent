#!/usr/bin/env -S uv run --quiet python3

"""Report a mutation testing kill-score for a given C source file.

Usage:
    % ./eval/mutants/compute_kill_score.py <PATH_TO_C_FILE_OR_DIR> \
            [--auto-include] \
            [--include-dirs] \
            [--mutation] \
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import IO

from loguru import logger
from tools.construct_call_graph import construct_call_graph
from tools.run_cbmc import run_cbmc
from tools.util.callgraph import CallGraph
from tools.util.mutation import BaselineFailsVerification, MutantVerificationResult, MutationScore, MutationTestingResult, NoMutantsGenerated, verify_mutant

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval.mutants.generate_mutants_and_compute_score import generate_mutants_and_compute_score
from eval.mutants.mutate_function import get_mutants
from eval.mutants.util import get_files_with_extension
from tools.util import get_functions_with_cprover_annotations, get_call_graph


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
    args = parser.parse_args()

    logger.remove()
    logger.add(sys.stderr, level="INFO")

    input_files = get_files_with_extension(args.path, ".c")
    if not input_files:
        logger.error(f"No .c files found at: {args.path}")
        sys.exit(1)

    include_dirs_from_cli = args.include_dirs or []
    for source in input_files:
        _process_file(
            source=source,
            auto_include=args.auto_include,
            include_dirs=include_dirs_from_cli,
        )
    return sys.exit(0)


def _process_file(
    source: Path,
    auto_include: bool,
    include_dirs: list[str],
) -> None:
    """Run all enabled metrics for a single source file and write JSONL records to `out`.

    Arguments:
        source (Path): The path to the file for evaluation.
        out (IO[str]): The output.
        auto_include (bool): True iff an `include` dir (i.e., a dir containing headers) should be
            automatically detected.
        include_dirs (list[str]): List of directories containing files (e.g., headers) that should
            be included in verification.
    """
    logger.info(f"Processing {source}")
    functions_in_file = sorted(get_call_graph(str(source)).keys())
    functions_in_file_with_cprover_annos = get_functions_with_cprover_annotations(str(source))

    if not functions_in_file:
        logger.warning(f"{source} had no functions to process")
        return

    include_dirs = (
        [*_autodetect_include_dirs(str(source)), *include_dirs] if auto_include else include_dirs
    )
    if include_dirs:
        logger.debug(f"[auto-include] using {include_dirs}")

    functions_to_mutation_testing_results: dict[str, dict] = {}
    for function in functions_in_file:
        try:
            mutation_testing_result_for_function = get_mutation_testing_result(source, function, functions_in_file_with_cprover_annos, include_dirs)
            functions_to_mutation_testing_results[function] = mutation_testing_result_for_function
        except Exception as e:
            # Don't let an error ruin a run.
            logger.warning(f"Error while computing kill-score for: {function};\n{e}")
            continue
    
    for result in functions_to_mutation_testing_results.values():
        print(result)
    

def get_mutation_testing_result(source_file: Path, function: str, annotated_functions: set[str], include_dirs: list[str]) -> dict:
    path_to_source = Path(source_file).resolve()
    result = {
        "function": function,
        "has_annotations": function in annotated_functions,
        "has_mutants": False,
        "is_verified": False,
        "num_mutants": 0,
        "num_killed": 0
    }
    mutants = get_mutants(str(path_to_source), function_name=function)
    result["num_mutants"] = len(mutants)
    result["has_mutants"] = len(mutants) > 0
    cbmc_result = run_cbmc(function, str(path_to_source), include_dirs)
    result["is_verified"] = cbmc_result.is_function_verified
    if not result["is_verified"]:
        # The function doesn't verify, no point going further.
        result["num_killed"] = 0
        return result
    
    # If the function verifies, see if it kills any mutants.
    call_graph = CallGraph(
        json.loads(Path(construct_call_graph(str(source_file))).read_text(encoding="utf-8"))
    )

    paths_to_mutants = {
        _get_path_for_mutated_source(path_to_source.parent, source_file, i): mutant
        for i, mutant in enumerate(mutants)
    }

    for i, (path, mutant) in enumerate(paths_to_mutants.items()):
        print(f"[mutation testing: {function}] ({i+1}/{len(mutants)})", file=sys.stderr, flush=True)
        mutant_vresult = verify_mutant(path, mutant, include_dirs, call_graph)
        if mutant_vresult.killed:
            result["num_killed"] += 1
    print(result)
    return result


def _get_path_for_mutated_source(
    workspace_path: Path, path_to_original_source: Path, index: int
) -> Path:
    """Return the path to which to write a mutated source file.

    For example, given the path `/app/test/data/foo.c`, return `/app/test/data/foo__mutant_1.c`

    Args:
        workspace_path (Path): The directory under which mutation testing occurs.
        path_to_original_source (Path): The path to the original source file.
        index (int): The index of the mutant, used as a identifier for the mutant source path.

    Returns:
        Path: The path to which to write a mutated source file.
    """
    return (
        workspace_path
        / f"{path_to_original_source.stem}__mutant_{index}{path_to_original_source.suffix}"
    )



def _mutation_testing_result_to_dict(result: MutationTestingResult) -> dict:
    result_dict = {
        "num_mutants": 0,
        "num_killed": 0
    }
    if isinstance(result, BaselineFailsVerification) or isinstance(result, NoMutantsGenerated):
        return result_dict
    if isinstance(result, MutationScore):
        result_dict["num_mutants"] = result.num_mutants
        result_dict["num_killed"] = result.num_killed
        return result_dict
    raise ValueError(f"Unexpected value for mutation testing result: {result}")


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
