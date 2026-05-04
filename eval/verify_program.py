#!/usr/bin/env -S uv run --quiet python3

"""Run CBMC on function in a C program with CBMC annotations and report pass/fail.

Usage: ./eval/verify_program.py <PATH_TO_C_FILE>
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.run_cbmc import get_cbmc_command
from tools.util import (
    build_stub_index,
    get_call_graph,
    get_functions_with_cprover_annotations,
    get_in_file_callees_for,
    get_unstubbed_external_callees_for,
)


@dataclass(frozen=True)
class VerificationResult:
    """Represent the result of running CBMC on a function.

    Attributes:
        function (str): The name of the function on which CBMC is run.
        returncode (int): The return code of the verification process.
        failures (list[str]): The lines from the CBMC output that are suffixed with "FAILURE".
    """

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
    parser = argparse.ArgumentParser(description="Run CBMC on every function in a C program.")
    parser.add_argument("file", help="Path to the C file to verify.")
    args = parser.parse_args()

    results = _verify_program(args.file)
    _print_summary(results)
    return 0 if all(result.passed for result in results) else 1


def _verify_program(file: str) -> list[VerificationResult]:
    """Return verification results for each specified function in the given file.

    Args:
        file (str): The C file to verify.

    Returns:
        list[VerificationResult]: The verification results for each specified function in the given
            file.
    """
    call_graph = get_call_graph(file)
    call_graph_path = f"{Path(file).stem}-callgraph.json"
    Path(call_graph_path).write_text(json.dumps(call_graph))

    stub_index = build_stub_index()
    annotated = get_functions_with_cprover_annotations(file)

    results: list[VerificationResult] = []
    for function in call_graph:
        if function not in annotated:
            print(f"[skip] {function} (no CBMC annotations)")
            continue
        nondet = get_unstubbed_external_callees_for(function, call_graph_path, stub_index)
        print(f"[verify] {function}" + (f"  (nondet: {', '.join(nondet)})" if nondet else ""))
        result = _verify_function(function, file, call_graph_path)
        status = "PASS" if result.passed else "FAIL"
        print(f"  -> {status} (returncode={result.returncode})")
        for failure in result.failures:
            print(f"     {failure}")
        results.append(result)
    return results


def _verify_function(function: str, file: str, call_graph_path: str) -> VerificationResult:
    """Return the result of verifying a function.

    Args:
        function (str): The function to verify.
        file (str): The file in which the function to verify is declared.
        call_graph_path (str): The path to the call graph.

    Returns:
        VerificationResult: The result of verifying a function.
    """
    callees = get_in_file_callees_for(function, call_graph_path)
    command = get_cbmc_command(function, callees, file)
    completed = subprocess.run(command, capture_output=True, text=True, shell=True, check=False)
    failures = [
        line.strip() for line in completed.stderr.splitlines() if line.strip().endswith("FAILURE")
    ]
    return VerificationResult(function, completed.returncode, failures)


def _print_summary(results: list[VerificationResult]) -> None:
    """Print a summary of verification results.

    Args:
        results (list[VerificationResult]): The verification results to summarize.
    """
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    print(f"Summary: {passed}/{total} functions verified")
    for r in results:
        marker = "ok" if r.passed else "FAIL"
        print(f"  [{marker}] {r.function}")


if __name__ == "__main__":
    main()
