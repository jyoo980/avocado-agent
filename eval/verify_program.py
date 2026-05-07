#!/usr/bin/env -S uv run --quiet python3

"""Run CBMC on every annotated function in a C file or directory of C files and report pass/fail.

Usage: ./eval/verify_program.py <PATH_TO_C_FILE_OR_DIRECTORY>
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

from tools.run_cbmc import get_cbmc_command, missing_body_for_callee
from tools.util import (
    build_stub_index,
    get_call_graph,
    get_functions_with_cprover_annotations,
    get_in_file_callees_for,
    get_stub_paths_for,
    get_unstubbed_external_callees_for,
)


@dataclass(frozen=True)
class VerificationResult:
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


def main() -> int:
    """Run CBMC on function in a C program with CBMC annotations and report pass/fail.

    Returns:
        int: 0 if all specified functions successfully verify, else 1.
    """
    parser = argparse.ArgumentParser(
        description="Run CBMC on every annotated function in a C file or directory of C files."
    )
    parser.add_argument("path", help="Path to a C file, or a directory containing C files.")
    parser.add_argument("-v", help="Enable debug logging", action="store_true")
    args = parser.parse_args()

    logger.remove()
    logger.add(sys.stderr, level="INFO")

    if args.v:
        logger.remove()
        logger.add(sys.stderr, level="DEBUG")

    files = _resolve_c_files(args.path)
    if not files:
        logger.info(f"No .c files found at: {args.path}")
        return 1

    results: list[VerificationResult] = []
    for file in files:
        logger.info(f"=== {file} ===")
        results.extend(_verify_program(str(file)))
    _print_summary(results)
    return 0 if all(result.passed for result in results) else 1


def _resolve_c_files(path_str: str) -> list[Path]:
    """Return the list of C files to verify for a given path.

    If the path is a directory, recursively collects all `.c` files within it. If the path is a
    file, returns it as a single-element list regardless of extension.

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


def _verify_program(file: str) -> list[VerificationResult]:
    """Return verification results for each specified function in the given file.

    Args:
        file (str): The C file to verify.

    Returns:
        list[VerificationResult]: The verification results for each specified function in the given
            file.
    """
    call_graph = get_call_graph(file)

    stub_index = build_stub_index()
    annotated = get_functions_with_cprover_annotations(file)

    results: list[VerificationResult] = []
    for function in call_graph:
        if function not in annotated:
            logger.info(f"[skip] {function} (no CBMC annotations)")
            continue
        nondet = get_unstubbed_external_callees_for(function, call_graph, stub_index)
        logger.debug(
            f"[verify] {function}" + (f"  (nondet: {', '.join(nondet)})" if nondet else "")
        )
        result = _verify_function(function, file, call_graph)
        status = "PASS" if result.passed else "FAIL"
        logger.debug(f"  -> {status} (returncode={result.returncode})")
        for failure in result.failures:
            logger.debug(f"     {failure}")
        results.append(result)
    return results


def _verify_function(function: str, file: str, call_graph: CallGraph) -> VerificationResult:
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
) -> VerificationResult:
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
    if completed.returncode != 0 and missing_body_for_callee(completed.stdout, completed.stderr):
        command = get_cbmc_command(
            function, callees, file, prevent_macro_expansion=True, stub_paths=stub_paths
        )
        logger.debug(command)
        completed = subprocess.run(command, capture_output=True, text=True, shell=True, check=False)
    failures = [
        line.strip() for line in completed.stderr.splitlines() if line.strip().endswith("FAILURE")
    ]
    return VerificationResult(file, function, completed.returncode, failures)


def _print_summary(results: list[VerificationResult]) -> None:
    """Print a summary of verification results.

    Args:
        results (list[VerificationResult]): The verification results to summarize.
    """
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    logger.info(f"Summary: {passed}/{total} functions verified")
    for r in results:
        marker = "ok" if r.passed else "FAIL"
        logger.info(f"  [{marker}] {r.file}::{r.function}")


if __name__ == "__main__":
    main()
