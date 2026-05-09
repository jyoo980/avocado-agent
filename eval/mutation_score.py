#!/usr/bin/env -S uv run --quiet python3

"""Compute the body-mutation kill rate (M2.1) for an annotated C function.

Workflow per function:
  1. Enumerate body mutants via `tools.mutate.enumerate_mutants` (operator swaps only —
     contract clauses are not touched).
  2. Write each mutant alongside the original source so its `#include` paths still resolve.
  3. Run CBMC on every mutant against the *original* spec via `tools.run_cbmc.run_cbmc`.
  4. A mutant is "killed" when CBMC fails (the spec caught the change) and "survives" when
     CBMC still verifies (the spec was too weak to observe the mutation).
  5. Aggregate per-function kill rate and emit JSONL.

Usage:
    eval/mutation_score.py --function <NAME> --file <PATH_TO_C_FILE> [--jsonl PATH]

This driver requires CBMC, goto-cc, and goto-instrument on PATH. The pure mutation engine
(`tools/mutate.py`) is independently testable without CBMC.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.mutate import Mutant, enumerate_mutants
from tools.run_cbmc import run_cbmc


@dataclass(frozen=True)
class MutantResult:
    """Verification outcome for a single mutant."""

    mutant: Mutant
    killed: bool
    returncode: int


@dataclass(frozen=True)
class MutationScore:
    """Aggregated mutation-kill statistics for one function."""

    file: str
    function: str
    total: int
    killed: int
    survived: int
    kill_rate: float
    results: list[MutantResult] = field(default_factory=list)


def score_function_mutations(
    file_path: str,
    function_name: str,
    workspace: Path | None = None,
    keep_artifacts: bool = False,
) -> MutationScore:
    """Score body-mutation kill rate for `function_name` in `file_path`.

    Mutant `.c` files are written next to the original source by default so relative
    `#include` resolution mirrors the unmutated build. Pass `workspace` to override the
    location. Files are removed unless `keep_artifacts=True` (useful for debugging).

    Args:
        file_path (str): Path to the C source defining the function.
        function_name (str): The function whose body should be mutated.
        workspace (Path | None): Directory to write mutant files into. Defaults to the
            source file's directory.
        keep_artifacts (bool): When True, mutant `.c` files are kept for inspection.

    Returns:
        MutationScore: Aggregated counts plus per-mutant verification results.
    """
    source_path = Path(file_path).resolve()
    workspace = workspace or source_path.parent
    workspace.mkdir(parents=True, exist_ok=True)

    mutants = enumerate_mutants(str(source_path), function_name)
    written: list[Path] = []
    results: list[MutantResult] = []
    try:
        for index, mutant in enumerate(mutants):
            mutant_path = workspace / f"{source_path.stem}__mutant_{index}{source_path.suffix}"
            mutant_path.write_text(mutant.mutant_source, encoding="utf-8")
            written.append(mutant_path)
            _, returncode = run_cbmc(
                function_to_verify=function_name,
                file_containing_function_to_verify=str(mutant_path),
            )
            results.append(
                MutantResult(mutant=mutant, killed=returncode != 0, returncode=returncode)
            )
    finally:
        if not keep_artifacts:
            for path in written:
                path.unlink(missing_ok=True)

    total = len(results)
    killed = sum(1 for r in results if r.killed)
    survived = total - killed
    kill_rate = (killed / total) if total else 0.0
    return MutationScore(
        file=str(source_path),
        function=function_name,
        total=total,
        killed=killed,
        survived=survived,
        kill_rate=round(kill_rate, 4),
        results=results,
    )


def main() -> int:
    """CLI entry point: score body-mutation kill rate for one function and emit JSONL.

    Returns:
        int: 0 on success.
    """
    parser = argparse.ArgumentParser(
        description="Run CBMC against body mutants of a function and report kill rate."
    )
    parser.add_argument("--function", required=True, help="Function whose body should be mutated.")
    parser.add_argument("--file", required=True, help="Path to the C file containing the function.")
    parser.add_argument(
        "--jsonl",
        default=None,
        help="Write per-mutant + summary JSONL records to this path (default: stdout).",
    )
    parser.add_argument(
        "--keep-artifacts",
        action="store_true",
        help="Keep mutant .c files for inspection. By default they are removed after scoring.",
    )
    args = parser.parse_args()

    score = score_function_mutations(
        file_path=args.file,
        function_name=args.function,
        keep_artifacts=args.keep_artifacts,
    )

    output_lines: list[str] = []
    output_lines.append(
        json.dumps(
            {
                "kind": "mutation_summary",
                "file": score.file,
                "function": score.function,
                "total": score.total,
                "killed": score.killed,
                "survived": score.survived,
                "kill_rate": score.kill_rate,
            }
        )
    )
    output_lines.extend(
        json.dumps(
            {
                "kind": "mutant_result",
                "file": score.file,
                "function": score.function,
                "operator_class": result.mutant.operator_class,
                "original": result.mutant.original,
                "replacement": result.mutant.replacement,
                "line": result.mutant.line,
                "column": result.mutant.column,
                "killed": result.killed,
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
