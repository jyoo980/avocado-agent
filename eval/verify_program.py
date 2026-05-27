#!/usr/bin/env -S uv run --quiet python3

"""Run CBMC on every function in a C file or directory of C files and report pass/fail.

Usage: ./eval/verify_program.py <PATH_TO_C_FILE_OR_DIRECTORY> \
                                [--auto-include] \
                                [--v] \
                                [--skip-unannotated-functions]

"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from tools.util import (
    build_stub_index,
    get_call_graph,
    get_functions_with_cprover_annotations,
    get_unstubbed_external_callees_for,
)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.run_cbmc import RunCbmcResult, run_cbmc


@dataclass(frozen=True)
class FunctionVerificationResult:
    """Represent the result of running CBMC on a function.

    Attributes:
        file (str): The C file containing the function on which CBMC is run.
        function (str): The name of the function on which CBMC is run.
        returncode (int): The return code of the verification process.
        failures (list[str]): The lines from the CBMC output that are suffixed with "FAILURE".
    """

    file: str
    function: str
    failures: list[str]
    run_cbmc_result: RunCbmcResult

    @property
    def is_function_verified(self) -> bool:
        """Return True iff the function is verified.

        Returns:
            bool: True iff the function is verified.
        """
        return self.run_cbmc_result.is_function_verified


@dataclass(frozen=True)
class ProgramVerificationResult:
    """Represent the result(s) of running CBMC on a program.

    Attributes:
        file (str): The C file containing the function on which CBMC is run.
        skipped_function_names (list[str]): The names of the functions for which verification was
            skipped.
        vresults (list[VerificationResult]): The verification results.
    """

    file: str
    skipped_function_names: list[str]
    vresults: list[FunctionVerificationResult]

    @property
    def passed(self) -> bool:
        """Return True iff all verification results are successful.

        Returns:
            bool: True iff all verification results are successful.
        """
        return all(vresult.is_function_verified for vresult in self.vresults)


def main() -> int:
    """Run CBMC on function in a C program and report pass/fail.

    Returns:
        int: 0 if all functions successfully verify, else 1.
    """
    parser = argparse.ArgumentParser(
        description="Run CBMC on every function in a C file or directory of C files."
    )
    parser.add_argument("path", help="Path to a C file, or a directory containing C files.")
    parser.add_argument("--v", help="Enable debug logging", action="store_true")
    parser.add_argument(
        "--skip-unannotated-functions",
        help="Skip running CBMC on unannotated functions",
        action="store_true",
    )
    parser.add_argument(
        "--auto-include",
        help=(
            "For each .c file, look for a sibling include/ directory at "
            "<source>/../include and pass it to CBMC as an include path. "
            "Fits projects whose headers live next to their src/ tree."
        ),
        action="store_true",
    )
    args = parser.parse_args()

    logger.remove()
    debug_level = "DEBUG" if args.v else "INFO"
    logger.add(sys.stderr, level=debug_level)

    files = _get_files_for_verification(args.path)
    if not files:
        logger.info(f"No .c files found at: {args.path}")
        return 1

    files_to_program_verification_results: dict[str, ProgramVerificationResult] = {}
    for file in files:
        logger.info(f"=== {file} ===")
        results_for_file = _verify_program(
            str(file),
            skip_unannotated_functions=args.skip_unannotated_functions,
            auto_include=args.auto_include,
        )
        if not results_for_file.vresults:
            logger.info("No specifications to verify.")
        files_to_program_verification_results[str(file)] = results_for_file
    _print_summary(files_to_program_verification_results)
    return (
        0 if all(result.passed for result in files_to_program_verification_results.values()) else 1
    )


def _get_files_for_verification(path_str: str) -> list[Path]:
    """Return the list of C files to verify for a given path.

    If the path is a directory, recursively collects all `.c` files underneath it, including
    sub-directories.

    Args:
        path_str (str): Path to a C file or a directory containing C files.

    Returns:
        list[Path]: Sorted list of paths to verify.
    """
    path = Path(path_str)
    if not path.exists():
        return []
    if path.is_dir():
        return sorted(path.rglob("*.c"))
    return [path] if path.suffix == ".c" else []


def _verify_program(
    file: str,
    skip_unannotated_functions: bool,
    auto_include: bool = False,
) -> ProgramVerificationResult:
    """Return the verification result for the given file.

    Args:
        file (str): The C file to verify.
        skip_unannotated_functions (bool): True iff unannotated functions should be skipped
            (i.e., CBMC should not be run on them).
        auto_include (bool): True iff `<file>/../include` should be added to CBMC's include
            search path when that directory exists. Defaults to False.

    Returns:
        ProgramVerificationResult: The verification result for the given file.
    """
    call_graph = get_call_graph(file)
    stub_index = build_stub_index()
    names_of_functions_to_verify = call_graph.keys()
    skipped_functions = {}

    if skip_unannotated_functions:
        names_of_functions_to_verify = get_functions_with_cprover_annotations(file)
        skipped_functions = set(call_graph.keys()).difference(names_of_functions_to_verify)

    include_dirs = _autodetect_include_dirs(file) if auto_include else []
    if include_dirs:
        logger.debug(f"[auto-include] using {include_dirs}")

    results: list[FunctionVerificationResult] = []
    for function in names_of_functions_to_verify:
        nondet_callees = get_unstubbed_external_callees_for(function, call_graph, stub_index)
        logger.debug(
            f"[verifying] {function}"
            + (f"  (nondet: {', '.join(nondet_callees)})" if nondet_callees else "")
        )
        result = _verify_function(function, file, include_dirs=include_dirs)
        status = str(result.run_cbmc_result)
        if status == "PASS":
            logger.debug(f"  -> {status} (returncode={result.run_cbmc_result.returncode})")
        else:
            logger.error(f"  -> {status} (returncode={result.run_cbmc_result.returncode})")
        for failure in result.failures:
            logger.error(f"     {failure}")
        results.append(result)
    return ProgramVerificationResult(file, list(skipped_functions), results)


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


def _verify_function(
    function: str,
    file: str,
    include_dirs: list[str] | None = None,
) -> FunctionVerificationResult:
    """Return the result of verifying a function.

    Self-recursive functions are tried inductively first (recursive call discharged by the
    function's own contract via `--replace-call-with-contract`); if that fails — typically because
    the contract is not inductive — fall back to bounded unwinding. Non-recursive functions are
    verified in a single attempt.

    Args:
        function (str): The function to verify.
        file (str): The file in which the function to verify is declared.
        include_dirs (list[str] | None): Directories to add to CBMC's include search path,
            forwarded to `run_cbmc`. Defaults to None.

    Returns:
        VerificationResult: The result of verifying a function.
    """
    result = run_cbmc(function, file, include_dirs=include_dirs)
    if result.timed_out:
        return FunctionVerificationResult(file, function, failures=[], run_cbmc_result=result)
    if result.cbmc_ran_successfully and not result.is_function_verified:
        failures = [
            line.strip()
            for line in result.response.splitlines()
            if line.strip().endswith("FAILURE")
        ]
        return FunctionVerificationResult(file, function, failures, result)
    return FunctionVerificationResult(file, function, failures=[], run_cbmc_result=result)


def _print_summary(files_to_results: dict[str, ProgramVerificationResult]) -> None:
    """Print a summary of program verification results.

    Args:
        files_to_results (dict[str, ProgramVerificationResult]): A dictionary of files to their
            verification results to summarize.
    """
    for file, program_verification_result in files_to_results.items():
        passed = sum(1 for r in program_verification_result.vresults if r.is_function_verified)
        total = len(program_verification_result.vresults)
        if total:
            logger.info(f"Summary ({file!s}): {passed}/{total} functions verified")
        for function_vresult in program_verification_result.vresults:
            marker = str(function_vresult.run_cbmc_result)
            if marker == "PASS":
                logger.info(f"  [{marker}] {function_vresult.file}::{function_vresult.function}")
            else:
                logger.error(f"  [{marker}] {function_vresult.file}::{function_vresult.function}")
        for skipped_f in program_verification_result.skipped_function_names:
            logger.warning(f"  [skipped, no specs] {program_verification_result.file}::{skipped_f}")


if __name__ == "__main__":
    main()
