#!/usr/bin/env -S uv run --quiet python3

"""Compute clause-removal redundancy (M2.3) for an annotated C function.

Workflow per function:
  1. Enumerate clause mutants via `tools.clause_mutate.enumerate_clause_mutants`.
  2. Write each mutant alongside the original source.
  3. Run CBMC against each mutant; if verification still passes, the removed clause was
     redundant for soundness. (It may still strengthen the spec for callers — see M1.2.)
  4. Aggregate per-function counts and emit JSONL.

Usage:
    eval/clause_redundancy.py --function <NAME> --file <PATH_TO_C_FILE> [--jsonl PATH]

Requires CBMC, goto-cc, and goto-instrument on PATH.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.clause_mutate import ClauseMutant, enumerate_clause_mutants
from tools.run_cbmc import run_cbmc


@dataclass(frozen=True)
class ClauseResult:
    """Verification outcome when one clause is removed."""

    clause: ClauseMutant
    redundant: bool
    returncode: int


@dataclass(frozen=True)
class RedundancyScore:
    """Aggregated clause-removal redundancy statistics for one function."""

    file: str
    function: str
    total_clauses: int
    redundant: int
    required: int
    redundancy_rate: float
    results: list[ClauseResult] = field(default_factory=list)


def score_clause_redundancy(
    file_path: str,
    function_name: str,
    workspace: Path | None = None,
    keep_artifacts: bool = False,
) -> RedundancyScore:
    """Score clause redundancy for `function_name` in `file_path`.

    Args:
        file_path (str): Path to the C source defining the function.
        function_name (str): The function whose contract clauses should be tested.
        workspace (Path | None): Directory to write mutant files into. Defaults to the
            source file's directory so relative `#include` paths still resolve.
        keep_artifacts (bool): When True, mutant `.c` files are kept for inspection.

    Returns:
        RedundancyScore: Aggregated counts plus the per-clause results.
    """
    source_path = Path(file_path).resolve()
    workspace = workspace or source_path.parent
    workspace.mkdir(parents=True, exist_ok=True)

    mutants = enumerate_clause_mutants(str(source_path), function_name)
    written: list[Path] = []
    results: list[ClauseResult] = []
    try:
        for index, mutant in enumerate(mutants):
            mutant_path = workspace / f"{source_path.stem}__clause_drop_{index}{source_path.suffix}"
            mutant_path.write_text(mutant.mutant_source, encoding="utf-8")
            written.append(mutant_path)
            _, returncode = run_cbmc(
                function_to_verify=function_name,
                file_containing_function_to_verify=str(mutant_path),
            )
            results.append(
                ClauseResult(clause=mutant, redundant=returncode == 0, returncode=returncode)
            )
    finally:
        if not keep_artifacts:
            for path in written:
                path.unlink(missing_ok=True)

    total = len(results)
    redundant = sum(1 for r in results if r.redundant)
    required = total - redundant
    rate = (redundant / total) if total else 0.0
    return RedundancyScore(
        file=str(source_path),
        function=function_name,
        total_clauses=total,
        redundant=redundant,
        required=required,
        redundancy_rate=round(rate, 4),
        results=results,
    )


def main() -> int:
    """CLI entry point: score clause redundancy for one function and emit JSONL.

    Returns:
        int: 0 on success.
    """
    parser = argparse.ArgumentParser(
        description="Run CBMC on clause-removal mutants and report redundancy."
    )
    parser.add_argument("--function", required=True)
    parser.add_argument("--file", required=True)
    parser.add_argument("--jsonl", default=None)
    parser.add_argument("--keep-artifacts", action="store_true")
    args = parser.parse_args()

    score = score_clause_redundancy(
        file_path=args.file,
        function_name=args.function,
        keep_artifacts=args.keep_artifacts,
    )

    output_lines: list[str] = [
        json.dumps(
            {
                "kind": "clause_redundancy_summary",
                "file": score.file,
                "function": score.function,
                "total_clauses": score.total_clauses,
                "redundant": score.redundant,
                "required": score.required,
                "redundancy_rate": score.redundancy_rate,
            }
        )
    ]
    output_lines.extend(
        json.dumps(
            {
                "kind": "clause_result",
                "file": score.file,
                "function": score.function,
                "clause_kind": result.clause.clause_kind,
                "clause_text": result.clause.clause_text,
                "line": result.clause.line,
                "column": result.clause.column,
                "redundant": result.redundant,
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
