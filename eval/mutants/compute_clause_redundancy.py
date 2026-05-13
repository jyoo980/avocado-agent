#!/usr/bin/env -S uv run --quiet python3

"""Compute clause-removal redundancy for an annotated C function.

Two redundancy verdicts are reported per clause:

  1. In-isolation: re-verify the mutated function with one clause removed. If CBMC still
     succeeds, the clause was redundant for the body's own correctness. (Largely vacuous for
     `__CPROVER_ensures` / `__CPROVER_assigns` / `__CPROVER_frees`: a weaker postcondition or
     frame is trivially satisfied by the body.)
  2. Caller-side: for each in-file caller of the mutated function, re-verify the caller while
     CBMC stubs the call site with the *weakened* contract. A clause is redundant for callers
     only if every in-file caller still verifies. Clauses on functions with no in-file callers
     are bucketed as "unobservable".

Cross-file callers are reported as `unobservable` because `run_cbmc` compiles a single source
file (`tools/run_cbmc.py`); extending it to accept extra source files would unlock cross-file
caller checks.

Usage:
    % ./eval/compute_clause_redundancy.py --function <NAME> --file <PATH_TO_C_FILE> [--jsonl PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from enum import StrEnum
from itertools import starmap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


from eval.mutants.mutate_specification import ClauseMutant, get_clause_mutants
from eval.mutants.util import check_expected_cbmc_return_code
from tools.construct_call_graph import construct_call_graph
from tools.run_cbmc import run_cbmc
from tools.util import get_in_file_callers_of
from tools.util.callgraph import CallGraph


class CallerSideVerdict(StrEnum):
    """Represent whether a specification is required or redundant for callers.

    A clause is redundant for callers iff all callees of the function with the mutated specification
    still verify. If a function with a mutated spec has no callees, no information can be obtained.
    """

    REDUNDANT_FOR_CALLERS = "redundant_for_callers"
    REQUIRED_BY_CALLERS = "required_by_callers"
    UNOBSERVABLE = "unobservable"


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
class CallerVerificationResult:
    """Result of verifying a single caller against a mutated callee contract.

    Attributes:
        caller (str): The caller function whose verification was retried.
        returncode (int): CBMC's return code for the caller under the weakened callee contract.
        successfully_verified (bool): True iff CBMC verified the caller (returncode == 0).
    """

    caller: str
    returncode: int
    successfully_verified: bool


@dataclass(frozen=True)
class ClauseCallerSideResult:
    """Caller-side verdict for one clause mutant (i.e., the removed clause).

    The verdict represents whether all callees verify even with the removed clause (the clause is
    redundant). A clause is not redundant if its removal results in at least one callee failing to
    verify.

    Attributes:
        removed_clause (ClauseMutant): The removed clause.
        caller_results (list[CallerVerificationResult]): Per-caller verification outcomes.
        verdict (CallerSideVerdict): Aggregated verdict over all in-file callers.
    """

    removed_clause: ClauseMutant
    caller_vresults: list[CallerVerificationResult]
    verdict: CallerSideVerdict


@dataclass(frozen=True)
class ClauseRedundancyScore:
    """Aggregated clause-removal redundancy statistics for a function.

    Attributes:
        file (str): The file for the mutant.
        function (str): The name of the function for which the specification was mutated.
        total_clauses (int): The total number of clauses for the function.
        num_redundant (int): The number of in-isolation-redundant clauses.
        num_required (int): The number of in-isolation-required clauses.
        redundancy_rate (float): num_redundant / total_clauses.
        results (list[ClauseRemovalVerificationResult]): In-isolation results per clause.
        num_redundant_for_callers (int): Clauses redundant for all in-file callers.
        num_required_by_callers (int): Clauses on which at least one in-file caller fails.
        num_unobservable (int): Clauses on functions with no in-file callers.
        caller_side_redundancy_rate (float): num_redundant_for_callers over observable clauses
            (total - num_unobservable); 0.0 when nothing is observable.
        caller_side_results (list[ClauseCallerSideResult]): Caller-side results per clause.
    """

    file: str
    function: str
    total_clauses: int
    num_redundant: int
    num_required: int
    redundancy_rate: float
    num_redundant_for_callers: int
    num_required_by_callers: int
    num_unobservable: int
    caller_side_redundancy_rate: float
    results: list[ClauseRemovalVerificationResult] = field(default_factory=list)
    caller_side_results: list[ClauseCallerSideResult] = field(default_factory=list)

    def summary(self) -> dict[str, str | int | float]:
        """Return a summary of this clause redundancy score.

        Returns:
            dict[str, str | int | float]: A summary of this clause redundancy score.
        """
        return {
            "kind": "clause_redundancy_summary",
            "file": self.file,
            "function": self.function,
            "total_clauses": self.total_clauses,
            "redundancy_rate": self.redundancy_rate,
            "caller_side_redundancy_rate": self.caller_side_redundancy_rate,
            "num_unobservable": self.num_unobservable,
        }


def main() -> None:
    """CLI entry point: score clause redundancy for one function and emit JSONL."""
    parser = argparse.ArgumentParser(
        description="Run CBMC on clause-removal mutants and report redundancy."
    )
    parser.add_argument("--function", required=True)
    parser.add_argument("--file", required=True)
    parser.add_argument("--jsonl", default=None)
    parser.add_argument("--keep-artifacts", action="store_true")
    args = parser.parse_args()

    clause_redundancy_score = compute_clause_redundancy_score(
        file_path=args.file,
        function_name=args.function,
        keep_artifacts=args.keep_artifacts,
    )

    if not clause_redundancy_score:
        sys.exit(1)

    removed_clause_to_caller_side_verdict = {
        (
            caller_side_vresult.removed_clause.line,
            caller_side_vresult.removed_clause.column,
            caller_side_vresult.removed_clause.clause_text,
        ): caller_side_vresult.verdict
        for caller_side_vresult in clause_redundancy_score.caller_side_results
    }

    output_lines: list[str] = [json.dumps(clause_redundancy_score.summary())]

    for in_isolation_vresult in clause_redundancy_score.results:
        removed_clause = in_isolation_vresult.clause
        key = (removed_clause.line, removed_clause.column, removed_clause.clause_text)
        output_lines.append(
            json.dumps(
                {
                    "kind": "clause_result",
                    "file": clause_redundancy_score.file,
                    "function": clause_redundancy_score.function,
                    "clause_kind": removed_clause.clause_kind,
                    "clause_text": removed_clause.clause_text,
                    "line": removed_clause.line,
                    "column": removed_clause.column,
                    "redundant": in_isolation_vresult.is_redundant,
                    "returncode": in_isolation_vresult.returncode,
                    "caller_side_verdict": removed_clause_to_caller_side_verdict[key],
                }
            )
        )

    for caller_side_result in clause_redundancy_score.caller_side_results:
        removed_clause = caller_side_result.removed_clause
        output_lines.extend(
            json.dumps(
                {
                    "kind": "caller_side_clause",
                    "file": clause_redundancy_score.file,
                    "function": clause_redundancy_score.function,
                    "clause_kind": removed_clause.clause_kind,
                    "clause_text": removed_clause.clause_text,
                    "line": removed_clause.line,
                    "column": removed_clause.column,
                    "caller": caller_vresults.caller,
                    "returncode": caller_vresults.returncode,
                    "still_verifies": caller_vresults.successfully_verified,
                }
            )
            for caller_vresults in caller_side_result.caller_vresults
        )
    body = "\n".join(output_lines) + "\n"
    if args.jsonl:
        Path(args.jsonl).write_text(body, encoding="utf-8")
    else:
        sys.stdout.write(body)


def compute_clause_redundancy_score(
    file_path: str,
    function_name: str,
    workspace: Path | None = None,
    keep_artifacts: bool = False,
) -> ClauseRedundancyScore | None:
    """Score clause redundancy for `function_name` in `file_path`.

    Mutant `.c` files are written next to the original source by default to simplify compilation
    and instrumentation with CBMC. Mutants are removed unless keep_artifacts is set to `True`.

    If the unmutated function does not verify, return None.

    Args:
        file_path (str): Path to the C source defining the function.
        function_name (str): The function whose contract clauses should be tested.
        workspace (Path | None): Directory to write mutant files into. Defaults to the
            source file's directory so relative `#include` paths still resolve.
        keep_artifacts (bool): When True, mutant `.c` files are kept for inspection.

    Returns:
        ClauseRedundancyScore: Aggregated counts plus the per-clause results.
    """
    source_path = Path(file_path).resolve()
    workspace = workspace or source_path.parent
    workspace.mkdir(parents=True, exist_ok=True)

    # Check that the original function verifies in the first place.
    _, returncode = run_cbmc(function_name, file_path)
    check_expected_cbmc_return_code(returncode)
    if returncode != 0:
        return None

    mutants = get_clause_mutants(str(source_path), function_name)
    paths_to_removed_clauses = {
        _get_path_to_source_with_removed_clause(workspace, source_path, i): mutant
        for i, mutant in enumerate(mutants)
    }

    in_file_callers = _get_in_file_callers(source_path, function_name)

    try:
        in_isolation_results = list(
            starmap(_verify_removed_clause_source, paths_to_removed_clauses.items())
        )
        caller_side_results = [
            _verify_callers(mutant_path, mutant, in_file_callers)
            for mutant_path, mutant in paths_to_removed_clauses.items()
        ]
    finally:
        if not keep_artifacts:
            for path in paths_to_removed_clauses:
                path.unlink(missing_ok=True)

    total = len(in_isolation_results)
    redundant = sum(1 for r in in_isolation_results if r.is_redundant)
    required = total - redundant
    rate = (redundant / total) if total else 0.0

    num_unobservable = sum(1 for r in caller_side_results if r.verdict == "unobservable")
    num_redundant_for_callers = sum(
        1 for r in caller_side_results if r.verdict == "redundant_for_callers"
    )
    num_required_by_callers = sum(
        1 for r in caller_side_results if r.verdict == "required_by_callers"
    )
    observable = total - num_unobservable
    caller_side_rate = (num_redundant_for_callers / observable) if observable else 0.0

    return ClauseRedundancyScore(
        file=str(source_path),
        function=function_name,
        total_clauses=total,
        num_redundant=redundant,
        num_required=required,
        redundancy_rate=round(rate, 4),
        num_redundant_for_callers=num_redundant_for_callers,
        num_required_by_callers=num_required_by_callers,
        num_unobservable=num_unobservable,
        caller_side_redundancy_rate=round(caller_side_rate, 4),
        results=in_isolation_results,
        caller_side_results=caller_side_results,
    )


def _get_in_file_callers(source_path: Path, function_name: str) -> list[str]:
    """Return functions in a file that directly call the function with the given name.

    Args:
        source_path (Path): The file in which to look up callers.
        function_name (str): The function for which to look up callers.

    Returns:
        list[str]: The names of the in-file callers of the given name.
    """
    call_graph_path = construct_call_graph(str(source_path))
    call_graph = CallGraph(json.loads(Path(call_graph_path).read_text(encoding="utf-8")))
    if function_name not in call_graph:
        msg = f"'{function_name}' was missing from the call graph"
        raise ValueError(msg)
    return get_in_file_callers_of(function_name, call_graph)


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
    """Return the result of verifying a removed-clause mutant in isolation.

    Args:
        path_to_write_removed_clause_mutant (Path): The path to which to write the source with the
            mutated specification.
        clause_mutant (ClauseMutant): The clause mutant.

    Returns:
        ClauseRemovalVerificationResult: The result of verifying a removed-clause mutant.
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


def _verify_callers(
    mutant_path: Path, clause_mutant: ClauseMutant, in_file_callers: list[str]
) -> ClauseCallerSideResult:
    """Return the caller-side verdict for one mutant.

    For each in-file caller, re-verify it against the mutated callee source so CBMC's
    `--replace-call-with-contract` uses the mutated contract. A clause is redundant for callers
    only if every caller still verifies; with no in-file callers, the verdict is `unobservable`.

    Args:
        mutant_path (Path): The path to the source code containing the mutant.
        clause_mutant (ClauseMutant): The clause mutant.
        in_file_callers (list[str]): The names of the in-file callers.

    Returns:
        ClauseCallerSideResult: The caller-side verification result for a mutated specification.
    """
    if not in_file_callers:
        return ClauseCallerSideResult(
            clause_mutant, caller_vresults=[], verdict=CallerSideVerdict.UNOBSERVABLE
        )

    caller_vresults: list[CallerVerificationResult] = []
    for caller in in_file_callers:
        _, returncode = run_cbmc(
            function_to_verify=caller,
            file_containing_function_to_verify=str(mutant_path),
        )
        check_expected_cbmc_return_code(returncode)
        caller_vresults.append(
            CallerVerificationResult(
                caller=caller, returncode=returncode, successfully_verified=returncode == 0
            )
        )

    verdict: CallerSideVerdict = (
        CallerSideVerdict.REDUNDANT_FOR_CALLERS
        if all(caller_vresult.successfully_verified for caller_vresult in caller_vresults)
        else CallerSideVerdict.REQUIRED_BY_CALLERS
    )
    return ClauseCallerSideResult(clause_mutant, caller_vresults=caller_vresults, verdict=verdict)


if __name__ == "__main__":
    sys.exit(main())
