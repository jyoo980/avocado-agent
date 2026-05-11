#!/usr/bin/env -S uv run --quiet python3

"""Compute clause-removal redundancy for an annotated C function.

Workflow per function:
  1. Enumerate clause mutants via `tools.clause_mutate.enumerate_clause_mutants`.
  2. Write each mutant alongside the original source.
  3. Run CBMC against each mutant; if verification still passes, the removed clause was
     redundant for soundness. (It may still strengthen the spec for callers — see M1.2.)
  4. Aggregate per-function counts and emit JSONL.

Usage:
    % ./eval/compute_clause_redundancy.py --function <NAME> --file <PATH_TO_C_FILE> [--jsonl PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


from itertools import starmap

from eval.mutants.mutate_specification import ClauseMutant, get_clause_mutants
from eval.mutants.util import check_expected_cbmc_return_code
from tools.run_cbmc import run_cbmc


@dataclass(frozen=True)
class ClauseRemovalVerificationResult:
    """Verification outcome when one clause is removed.

    Attributes:
        clause (ClauseMutant): The clause mutant.
        is_redundant (bool): True iff the clause was redundant (i.e., the function still verified
            under the mutated spec without the clause).
        returncode (int): The return code of the CBMC command used to verify the mutant.
    """

    clause: ClauseMutant
    is_redundant: bool
    returncode: int


@dataclass(frozen=True)
class ClauseRedundancyScore:
    """Aggregated clause-removal redundancy statistics for a function.

    Attributes:
        file (str): The file for the mutant.
        function (str): The name of the function for which the specification was mutated.
        total_clauses (int): The total number of clauses for the function.
        num_redundant (int): The number of redundant clauses.
        num_required (int): The number of required clauses.
        redundancy_rate (float): The number of redundant clauses / total clauses.
        results (list[ClauseRemovalVerificationResult]): The result of verifying each mutated
            specification with removed clauses.

    """

    file: str
    function: str
    total_clauses: int
    num_redundant: int
    num_required: int
    redundancy_rate: float
    results: list[ClauseRemovalVerificationResult] = field(default_factory=list)


def compute_clause_redundancy_score(
    file_path: str,
    function_name: str,
    workspace: Path | None = None,
    keep_artifacts: bool = False,
) -> ClauseRedundancyScore:
    """Score clause redundancy for `function_name` in `file_path`.

    Mutant `.c` files are written next to the original source by default to simplify compilation
    and instrumentation with CBMC. Mutants are removed unless keep_artifacts is set to `True`.

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

    mutants = get_clause_mutants(str(source_path), function_name)
    removed_clause_mutant_vresults: list[ClauseRemovalVerificationResult] = []
    paths_to_removed_clauses = {
        _get_path_to_source_with_removed_clause(workspace, source_path, i): mutant
        for i, mutant in enumerate(mutants)
    }

    # TODO: Check if the original function verifies.

    try:
        removed_clause_mutant_vresults = list(
            starmap(_verify_removed_clause_source, paths_to_removed_clauses.items())
        )
    finally:
        if not keep_artifacts:
            for path in paths_to_removed_clauses:
                path.unlink(missing_ok=True)

    total = len(removed_clause_mutant_vresults)
    redundant = sum(1 for r in removed_clause_mutant_vresults if r.is_redundant)
    required = total - redundant
    rate = (redundant / total) if total else 0.0
    return ClauseRedundancyScore(
        file=str(source_path),
        function=function_name,
        total_clauses=total,
        num_redundant=redundant,
        num_required=required,
        redundancy_rate=round(rate, 4),
        results=removed_clause_mutant_vresults,
    )


def _get_path_to_source_with_removed_clause(
    workspace_path: Path, path_to_original_source: Path, index: int
) -> Path:
    """Return the path to which to write a source file with a removed clause.

    For example, given the path `/app/test/data/foo.c`, return `/app/test/data/foo__clause_drop_1.c`

    Args:
        workspace_path (Path): The directory under redundancy score computation occurs.
        path_to_original_source (Path): The path to the original source file.
        index (int): The index of the mutant, used as a identifier for the mutant source path.

    Returns:
        Path: The path to which to write a mutated source file.
    """
    return (
        workspace_path
        / f"{path_to_original_source.stem}__clause_drop_{index}{path_to_original_source.suffix}"
    )


def _verify_removed_clause_source(
    path_to_write_removed_clause_mutant: Path, clause_mutant: ClauseMutant
) -> ClauseRemovalVerificationResult:
    """Return the result of verifying a removed-clause mutant.

    Args:
        path_to_write_removed_clause_mutant (Path): The path to which the mutated source is written.
        clause_mutant (ClauseMutant): The mutant.

    Returns:
        ClauseRemovalVerificationResult: The result of verifying a mutant.
    """
    path_to_write_removed_clause_mutant.write_text(clause_mutant.mutant_source, encoding="utf-8")

    _, returncode = run_cbmc(
        function_to_verify=clause_mutant.function,
        file_containing_function_to_verify=str(path_to_write_removed_clause_mutant),
    )
    check_expected_cbmc_return_code(returncode)
    return ClauseRemovalVerificationResult(
        clause_mutant, is_redundant=returncode == 0, returncode=returncode
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

    score = compute_clause_redundancy_score(
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
                "redundant": score.num_redundant,
                "required": score.num_required,
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
                "redundant": result.is_redundant,
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
