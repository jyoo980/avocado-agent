#!/usr/bin/env -S uv run --quiet python3

"""Caller-side replaceability (M1.2) for an annotated C function.

A function's contract is *modularly usable* when its annotated callers can be verified
using the contract in place of the function body. CBMC already does this when invoked on
the caller — `tools.run_cbmc.run_cbmc` automatically passes
`--replace-call-with-contract` for every in-file callee — so the metric is simply: of the
function's annotated callers, what fraction still verify?

Workflow per function `f`:
  1. Build the file's call graph and find callers of `f`.
  2. Filter to callers that themselves have CBMC contracts (CBMC needs the caller to have
     its own contract to verify it; unannotated callers are reported separately).
  3. Run CBMC on each annotated caller. Aggregate pass-rate.

Usage:
    eval/caller_replaceability.py --function <NAME> --file <PATH_TO_C_FILE> [--jsonl PATH]

Requires CBMC, goto-cc, and goto-instrument on PATH.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.run_cbmc import run_cbmc
from tools.util import (
    get_call_graph,
    get_functions_with_cprover_annotations,
)

if TYPE_CHECKING:
    from tools.util.callgraph import CallGraph


@dataclass(frozen=True)
class CallerResult:
    """Verification outcome for a single caller using `f`'s contract."""

    caller: str
    passed: bool
    returncode: int


@dataclass(frozen=True)
class ReplaceabilityScore:
    """Aggregated caller-replaceability statistics for one function."""

    file: str
    function: str
    annotated_callers: list[str]
    unannotated_callers: list[str]
    passed: int
    failed: int
    pass_rate: float
    results: list[CallerResult] = field(default_factory=list)


def get_callers_of(function_name: str, call_graph: CallGraph) -> list[str]:
    """Return the in-file callers of `function_name` (sorted, de-duplicated).

    Args:
        function_name (str): Name of the callee whose callers should be found.
        call_graph (CallGraph): The file's call graph.

    Returns:
        list[str]: Sorted, de-duplicated names of in-file callers.
    """
    return sorted(
        {caller for caller, callees in call_graph.items() if function_name in callees.internal}
    )


def score_caller_replaceability(file_path: str, function_name: str) -> ReplaceabilityScore:
    """Verify every annotated caller of `function_name` using its contract.

    Args:
        file_path (str): Path to the C source defining the function.
        function_name (str): The function whose callers should be re-verified with its
            contract substituted for the body.

    Returns:
        ReplaceabilityScore: Aggregated counts and per-caller verification results.
    """
    source_path = Path(file_path).resolve()
    call_graph = get_call_graph(str(source_path))
    if function_name not in call_graph:
        return ReplaceabilityScore(
            file=str(source_path),
            function=function_name,
            annotated_callers=[],
            unannotated_callers=[],
            passed=0,
            failed=0,
            pass_rate=0.0,
        )

    annotated = get_functions_with_cprover_annotations(str(source_path))
    callers = get_callers_of(function_name, call_graph)
    annotated_callers = [c for c in callers if c in annotated]
    unannotated_callers = [c for c in callers if c not in annotated]

    results: list[CallerResult] = []
    for caller in annotated_callers:
        _, returncode = run_cbmc(
            function_to_verify=caller,
            file_containing_function_to_verify=str(source_path),
        )
        results.append(CallerResult(caller=caller, passed=returncode == 0, returncode=returncode))

    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed
    pass_rate = (passed / len(results)) if results else 0.0
    return ReplaceabilityScore(
        file=str(source_path),
        function=function_name,
        annotated_callers=annotated_callers,
        unannotated_callers=unannotated_callers,
        passed=passed,
        failed=failed,
        pass_rate=round(pass_rate, 4),
        results=results,
    )


def main() -> int:
    """CLI entry point: score caller-side replaceability for one function and emit JSONL.

    Returns:
        int: 0 on success.
    """
    parser = argparse.ArgumentParser(
        description="Score caller-side replaceability (M1.2) for an annotated function."
    )
    parser.add_argument("--function", required=True)
    parser.add_argument("--file", required=True)
    parser.add_argument("--jsonl", default=None)
    args = parser.parse_args()

    score = score_caller_replaceability(file_path=args.file, function_name=args.function)

    output_lines: list[str] = [
        json.dumps(
            {
                "kind": "caller_replaceability_summary",
                "file": score.file,
                "function": score.function,
                "annotated_callers": score.annotated_callers,
                "unannotated_callers": score.unannotated_callers,
                "passed": score.passed,
                "failed": score.failed,
                "pass_rate": score.pass_rate,
            }
        )
    ]
    output_lines.extend(
        json.dumps(
            {
                "kind": "caller_result",
                "file": score.file,
                "function": score.function,
                "caller": result.caller,
                "passed": result.passed,
                "returncode": result.returncode,
            }
        )
        for result in score.results
    )
    body = "\n".join(output_lines) + "\n"
    if args.jsonl:
        Path(args.jsonl).write_text(body, encoding="utf-8")
    else:
        sys.stdout.write(body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
