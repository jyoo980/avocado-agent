#!/usr/bin/env -S uv run --quiet python3

"""Run CBMC on every annotated function in a C file or directory of C files and report pass/fail.

Usage: ./eval/verify_program.py <PATH_TO_C_FILE_OR_DIRECTORY> \
                                [--v] \
                                [--skip-unannotated-functions]

"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from tools.util.callgraph import CallGraph

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.run_cbmc import get_cbmc_command, has_missing_body_for_callee_message
from tools.util import (
    build_stub_index,
    get_call_graph,
    get_functions_with_cprover_annotations,
    get_in_file_callees_for,
    get_stub_paths_for,
    get_unstubbed_external_callees_for,
)


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
    returncode: int
    failures: list[str]

    @property
    def passed(self) -> bool:
        """Return True iff the returncode is 0.

        Returns:
            bool: True iff the returncode is 0.
        """
        return self.returncode == 0


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
        return all(vresult.passed for vresult in self.vresults)


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
    args = parser.parse_args()

    logger.remove()
    debug_level = "DEBUG" if args.v else "INFO"
    logger.add(sys.stderr, level=debug_level)

    files = _get_files_for_verification(args.path)
    if not files:
        logger.info(f"No .c files found at: {args.path}")
        return 1

    results: list[ProgramVerificationResult] = []
    for file in files:
        logger.info(f"=== {file} ===")
        results.append(
            _verify_program(str(file), skip_unannotated_functions=args.skip_unannotated_functions)
        )
    _print_summary(results)
    return 0 if all(result.passed for result in results) else 1


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


def _verify_program(file: str, skip_unannotated_functions: bool) -> ProgramVerificationResult:
    """Return the verification result for the given file.

    Args:
        file (str): The C file to verify.
        skip_unannotated_functions (bool): True iff unannotated functions should be skipped
            (i.e., CBMC should not be run on them).

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

    results: list[FunctionVerificationResult] = []
    for function in names_of_functions_to_verify:
        nondet_callees = get_unstubbed_external_callees_for(function, call_graph, stub_index)
        logger.debug(
            f"[verify] {function}"
            + (f"  (nondet: {', '.join(nondet_callees)})" if nondet_callees else "")
        )
        result = _verify_function(function, file, call_graph)
        status = "PASS" if result.passed else "FAIL"
        logger.debug(f"  -> {status} (returncode={result.returncode})")
        for failure in result.failures:
            logger.debug(f"     {failure}")
        results.append(result)
    return ProgramVerificationResult(file, list(skipped_functions), results)


def _verify_function(function: str, file: str, call_graph: CallGraph) -> FunctionVerificationResult:
    """Return the result of verifying a function.

    Self-recursive functions are tried inductively first (recursive call discharged by the
    function's own contract via `--replace-call-with-contract`); if that fails — typically because
    the contract is not inductive — fall back to bounded unwinding. Non-recursive functions are
    verified in a single attempt.

    Args:
        function (str): The function to verify.
        file (str): The file in which the function to verify is declared.
        call_graph (CallGraph): The call graph.

    Returns:
        VerificationResult: The result of verifying a function.
    """
    is_recursive = function in call_graph.get_callees(function).internal
    if is_recursive:
        result = _run_cbmc(function, file, call_graph, replace_self=True)
        if result.passed:
            return result
    return _run_cbmc(function, file, call_graph, replace_self=False)


def _run_cbmc(
    function: str, file: str, call_graph: CallGraph, replace_self: bool
) -> FunctionVerificationResult:
    """Run CBMC on `function` once and return the result.

    Args:
        function (str): The function to verify.
        file (str): The file in which the function to verify is declared.
        call_graph (CallGraph): The call graph.
        replace_self (bool): When True, `function` is included in the `--replace-call-with-contract`
            list so its recursive calls are discharged by its own contract.

    Returns:
        VerificationResult: The result of running CBMC on `function`.
    """
    callees = get_in_file_callees_for(function, call_graph, include_self=replace_self)
    stub_index = build_stub_index()
    stub_paths = get_stub_paths_for(function, call_graph, stub_index)

    # Try running the base CBMC command.
    command = get_cbmc_command(function, callees, file, stub_paths=stub_paths)
    logger.debug(command)
    completed = subprocess.run(command, capture_output=True, text=True, shell=True, check=False)

    # On failure, re-run without macro expansion if the error contains a message about missing
    # callee bodies.
    if completed.returncode != 0 and has_missing_body_for_callee_message(
        completed.stdout, completed.stderr
    ):
        command = get_cbmc_command(
            function, callees, file, prevent_macro_expansion=True, stub_paths=stub_paths
        )
        logger.debug(command)
        completed = subprocess.run(command, capture_output=True, text=True, shell=True, check=False)
    failures = [
        line.strip() for line in completed.stderr.splitlines() if line.strip().endswith("FAILURE")
    ]
    return FunctionVerificationResult(file, function, completed.returncode, failures)


def _print_summary(program_verification_results: list[ProgramVerificationResult]) -> None:
    """Print a summary of program verification results.

    Args:
        program_verification_results (list[ProgramVerificationResult]): The verification results to
            summarize.
    """
    for program_verification_result in program_verification_results:
        passed = sum(1 for r in program_verification_result.vresults if r.passed)
        total = len(program_verification_result.vresults)
        logger.info(f"Summary: {passed}/{total} functions verified")
        for r in program_verification_result.vresults:
            marker = "ok" if r.passed else "FAIL"
            logger.info(f"  [{marker}] {r.file}::{r.function}")
        for skipped_f in program_verification_result.skipped_function_names:
            logger.info(f"[skipped, no specs] {program_verification_result.file}::{skipped_f}")


if __name__ == "__main__":
    main()
