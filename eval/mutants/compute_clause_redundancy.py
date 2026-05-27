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
    % ./eval/compute_clause_redundancy.py --function <NAME> \
        --file <PATH_TO_C_FILE> \
        [--keep-artifacts]
        [--jsonl PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
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

# Matches the GNU `timeout(1)` convention used elsewhere; surfaces in `returncode` for
# timed-out CBMC runs so the value is distinguishable from CBMC's real 0/10 exit codes.
_TIMEOUT_RETURNCODE = 124


class CallerSideVerdict(StrEnum):
    """Represent whether a specification is required or redundant for callers.

    A clause is redundant for callers iff all callees of the function with the mutated specification
    still verify. If a function with a mutated spec has no callers, no information can be obtained.
    `INDETERMINATE_TIMEOUT` is reported when any caller verification timed out — the clause's
    caller-side status cannot be decided and the clause is excluded from the redundancy rate.
    """

    REDUNDANT_FOR_CALLERS = "redundant_for_callers"
    REQUIRED_BY_CALLERS = "required_by_callers"
    UNOBSERVABLE = "unobservable"
    UNVERIFIABLE_BASELINE = "unverifiable_baseline"
    INDETERMINATE_TIMEOUT = "indeterminate_timeout"


@dataclass(frozen=True)
class ClauseRemovalVerificationResult:
    """Verification outcome when one clause is removed.

    Attributes:
        clause (ClauseMutant): The clause mutant.
        is_redundant (bool): True iff the clause was redundant (i.e., the function still verified
            under the mutated spec without the clause). Always False when `timed_out` is True.
        returncode (int): The return code of the CBMC command used to verify the mutant. For
            timed-out runs this is the timeout sentinel (124), not a real CBMC exit code.
        timed_out (bool): True iff CBMC exceeded its per-attempt timeout for this clause removal.
            Timed-out runs are excluded from the in-isolation redundancy denominator.
    """

    clause: ClauseMutant
    is_redundant: bool
    returncode: int
    timed_out: bool = False


@dataclass(frozen=True)
class CallerVerificationResult:
    """Result of verifying a single caller against a mutated callee contract.

    Attributes:
        caller (str): The caller function whose verification was retried.
        returncode (int): CBMC's return code for the caller under the weakened callee contract.
            For timed-out runs this is the timeout sentinel (124).
        successfully_verified (bool): True iff CBMC verified the caller (returncode == 0).
            Always False when `timed_out` is True.
        timed_out (bool): True iff CBMC exceeded its per-attempt timeout for this caller.
    """

    caller: str
    returncode: int
    successfully_verified: bool
    timed_out: bool = False


@dataclass(frozen=True)
class ClauseCallerSideResult:
    """Caller-side verdict for one clause mutant (i.e., the removed clause).

    The verdict represents whether all callees verify even with the removed clause (the clause is
    redundant). A clause is not redundant if its removal results in at least one callee failing to
    verify.

    Attributes:
        removed_clause (ClauseMutant): The removed clause.
        caller_vresults (list[CallerVerificationResult]): Per-caller verification outcomes.
        verdict (CallerSideVerdict): Aggregated verdict over all in-file callers.
    """

    removed_clause: ClauseMutant
    caller_vresults: list[CallerVerificationResult]
    verdict: CallerSideVerdict


@dataclass(frozen=True)
class ClauseRedundancyScore:
    """Aggregated clause-removal redundancy statistics for a function.

    Both rates are reported over *decided* clauses — timed-out runs are excluded from
    their respective denominators so a single slow CBMC run cannot move the rate.

    Attributes:
        file (str): The file for the mutant.
        function (str): The name of the function for which the specification was mutated.
        total_clauses (int): The total number of clauses for the function.
        num_redundant_in_isolation (int): The number of in-isolation-redundant clauses.
        num_required_in_isolation (int): The number of in-isolation-required clauses.
        num_in_isolation_timed_out (int): In-isolation runs that exceeded the CBMC timeout.
            Excluded from the in-isolation redundancy denominator.
        in_isolation_redundancy_rate (float): num_redundant_in_isolation /
            (total_clauses - num_in_isolation_timed_out); 0.0 when nothing was decided.
        results (list[ClauseRemovalVerificationResult]): In-isolation results per clause.
        num_redundant_for_callers (int): Clauses redundant for all in-file callers.
        num_required_by_callers (int): Clauses on which at least one in-file caller fails.
        num_unobservable (int): Clauses on functions with no in-file callers.
        num_unverifiable_baseline (int): Clauses on functions whose only in-file callers already
            fail to verify on the unmutated source, so caller-side redundancy is not observable.
        num_indeterminate_timeout (int): Clauses where ≥1 caller verification timed out under
            the mutated contract. Excluded from the caller-side observable denominator.
        caller_side_redundancy_rate (float): num_redundant_for_callers over observable clauses
            (total - num_unobservable - num_unverifiable_baseline - num_indeterminate_timeout);
            0.0 when nothing is observable.
        caller_side_results (list[ClauseCallerSideResult]): Caller-side results per clause.
        unverifiable_baseline_callers (list[str]): In-file callers that failed to verify on the
            unmutated source and were therefore excluded from caller-side judgement.
    """

    file: str
    function: str
    total_clauses: int
    num_redundant_in_isolation: int
    num_required_in_isolation: int
    num_in_isolation_timed_out: int
    in_isolation_redundancy_rate: float
    num_redundant_for_callers: int
    num_required_by_callers: int
    num_unobservable: int
    num_unverifiable_baseline: int
    num_indeterminate_timeout: int
    caller_side_redundancy_rate: float
    results: list[ClauseRemovalVerificationResult] = field(default_factory=list)
    caller_side_results: list[ClauseCallerSideResult] = field(default_factory=list)
    unverifiable_baseline_callers: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, str | int | float | list[str]]:
        """Return a summary of this clause redundancy score.

        Returns:
            dict[str, str | int | float | list[str]]: A summary of this clause redundancy score.
        """
        return {
            "kind": "clause_redundancy_summary",
            "file": self.file,
            "function": self.function,
            "total_clauses": self.total_clauses,
            "in_isolation_redundancy_rate": self.in_isolation_redundancy_rate,
            "caller_side_redundancy_rate": self.caller_side_redundancy_rate,
            "num_unobservable": self.num_unobservable,
            "num_unverifiable_baseline": self.num_unverifiable_baseline,
            "num_in_isolation_timed_out": self.num_in_isolation_timed_out,
            "num_indeterminate_timeout": self.num_indeterminate_timeout,
            "unverifiable_baseline_callers": self.unverifiable_baseline_callers,
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
                    "timed_out": in_isolation_vresult.timed_out,
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
                    "timed_out": caller_vresults.timed_out,
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
    result = run_cbmc(function_name, file_path)
    if not result.is_function_verified:
        # No usable baseline; scoring would be meaningless without verifying the unmutated source.
        return None
    check_expected_cbmc_return_code(result.returncode)
    if result.returncode != 0:
        return None

    mutants = get_clause_mutants(str(source_path), function_name)
    paths_to_removed_clauses = {
        _get_path_to_source_with_removed_clause(workspace, source_path, i): mutant
        for i, mutant in enumerate(mutants)
    }

    in_file_callers = _get_in_file_callers(source_path, function_name)
    in_file_callers_to_baseline_verification_results = (
        _get_baseline_verification_results_for_callers(in_file_callers, source_path)
    )

    try:
        in_isolation_results = list(
            starmap(_verify_removed_clause_source, paths_to_removed_clauses.items())
        )
        caller_side_results = [
            _verify_callers(
                mutant_path,
                mutant,
                in_file_callers,
                in_file_callers_to_baseline_verification_results,
            )
            for mutant_path, mutant in paths_to_removed_clauses.items()
        ]
    finally:
        if not keep_artifacts:
            for path in paths_to_removed_clauses:
                path.unlink(missing_ok=True)

    return _aggregate_clause_redundancy_score(
        in_isolation_results,
        caller_side_results,
        in_file_callers_to_baseline_verification_results,
        file=str(source_path),
        function=function_name,
    )


def _aggregate_clause_redundancy_score(
    in_isolation_results: list[ClauseRemovalVerificationResult],
    caller_side_results: list[ClauseCallerSideResult],
    in_file_callers_to_baseline_verification_results: dict[str, bool],
    *,
    file: str,
    function: str,
) -> ClauseRedundancyScore:
    """Aggregate per-clause results into a ClauseRedundancyScore.

    Timed-out runs are excluded from both rate denominators (in-isolation excludes
    `timed_out` results; caller-side excludes `INDETERMINATE_TIMEOUT` in addition to
    the existing `UNOBSERVABLE` / `UNVERIFIABLE_BASELINE`).

    Args:
        in_isolation_results (list[ClauseRemovalVerificationResult]): Per-clause in-isolation
            verification outcomes.
        caller_side_results (list[ClauseCallerSideResult]): Per-clause caller-side outcomes.
        in_file_callers_to_baseline_verification_results (dict[str, bool]): Baseline outcome
            for each in-file caller on the unmutated source.
        file (str): The source file containing the function.
        function (str): The function whose clauses were tested.

    Returns:
        ClauseRedundancyScore: The aggregated score.
    """
    total = len(in_isolation_results)
    num_in_isolation_timed_out = sum(1 for r in in_isolation_results if r.timed_out)
    num_redundant_in_isolation = sum(1 for r in in_isolation_results if r.is_redundant)
    required = total - num_redundant_in_isolation - num_in_isolation_timed_out
    in_isolation_decided = total - num_in_isolation_timed_out
    in_isolation_redundancy_rate = (
        (num_redundant_in_isolation / in_isolation_decided) if in_isolation_decided else 0.0
    )

    verdict_counts: dict[CallerSideVerdict, int] = Counter(
        [result.verdict for result in caller_side_results]
    )
    observable = (
        total
        - verdict_counts[CallerSideVerdict.UNOBSERVABLE]
        - verdict_counts[CallerSideVerdict.UNVERIFIABLE_BASELINE]
        - verdict_counts[CallerSideVerdict.INDETERMINATE_TIMEOUT]
    )
    caller_side_rate = (
        (verdict_counts[CallerSideVerdict.REDUNDANT_FOR_CALLERS] / observable)
        if observable
        else 0.0
    )

    return ClauseRedundancyScore(
        file=file,
        function=function,
        total_clauses=total,
        num_redundant_in_isolation=num_redundant_in_isolation,
        num_required_in_isolation=required,
        num_in_isolation_timed_out=num_in_isolation_timed_out,
        in_isolation_redundancy_rate=round(in_isolation_redundancy_rate, 4),
        num_redundant_for_callers=verdict_counts[CallerSideVerdict.REDUNDANT_FOR_CALLERS],
        num_required_by_callers=verdict_counts[CallerSideVerdict.REQUIRED_BY_CALLERS],
        num_unobservable=verdict_counts[CallerSideVerdict.UNOBSERVABLE],
        num_unverifiable_baseline=verdict_counts[CallerSideVerdict.UNVERIFIABLE_BASELINE],
        num_indeterminate_timeout=verdict_counts[CallerSideVerdict.INDETERMINATE_TIMEOUT],
        caller_side_redundancy_rate=round(caller_side_rate, 4),
        results=in_isolation_results,
        caller_side_results=caller_side_results,
        unverifiable_baseline_callers=[
            caller
            for caller, is_verified in in_file_callers_to_baseline_verification_results.items()
            if not is_verified
        ],
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

    result = run_cbmc(
        function_to_verify=clause_mutant.function,
        file_containing_function_to_verify=str(path_to_write_removed_clause_mutant),
    )
    if result.timed_out:
        return ClauseRemovalVerificationResult(
            clause_mutant,
            is_redundant=False,
            returncode=_TIMEOUT_RETURNCODE,
            timed_out=True,
        )
    check_expected_cbmc_return_code(result.returncode)
    return ClauseRemovalVerificationResult(
        clause_mutant, is_redundant=result.is_function_verified, returncode=result.returncode
    )


def _get_baseline_verification_results_for_callers(
    in_file_callers: list[str], source_path: Path
) -> dict[str, bool]:
    """Return a map from in-file caller name to whether it verifies on the unmutated source.

    Run once before mutation so caller-side verdicts can be judged against each caller's
    baseline rather than against absolute CBMC success.

    Args:
        in_file_callers (list[str]): The names of the in-file callers.
        source_path (Path): The unmutated source file.

    Returns:
        dict[str, bool]: Map from caller name to True iff CBMC verified it on the unmutated source.
    """
    baselines: dict[str, bool] = {}
    for caller in in_file_callers:
        result = run_cbmc(
            function_to_verify=caller,
            file_containing_function_to_verify=str(source_path),
        )
        if result.timed_out:
            # Caller has no usable baseline; falls into the existing "not verifying" bucket
            # and will be excluded from caller-side judgement via unverifiable_baseline_callers.
            baselines[caller] = False
            continue
        check_expected_cbmc_return_code(result.returncode)
        baselines[caller] = result.is_function_verified
    return baselines


def _verify_callers(
    mutant_path: Path,
    clause_mutant: ClauseMutant,
    in_file_callers: list[str],
    caller_baselines: dict[str, bool],
) -> ClauseCallerSideResult:
    """Return the caller-side verdict for one mutant.

    For each in-file caller that verifies on the unmutated source, re-verify it against the
    mutated callee source so CBMC's `--replace-call-with-contract` uses the mutated contract.
    A clause is redundant for callers only if every baselined-verifying caller still verifies.
    With no in-file callers the verdict is `unobservable`; if callers exist but none verify on
    the unmutated source the verdict is `unverifiable_baseline`.

    Args:
        mutant_path (Path): The path to the source code containing the mutant.
        clause_mutant (ClauseMutant): The clause mutant.
        in_file_callers (list[str]): The names of the in-file callers.
        caller_baselines (dict[str, bool]): Map from caller name to baseline verification result
            on the unmutated source.

    Returns:
        ClauseCallerSideResult: The caller-side verification result for a mutated specification.
    """
    if not in_file_callers:
        return ClauseCallerSideResult(
            clause_mutant, caller_vresults=[], verdict=CallerSideVerdict.UNOBSERVABLE
        )

    verifying_callers = [caller for caller in in_file_callers if caller_baselines.get(caller)]
    if not verifying_callers:
        return ClauseCallerSideResult(
            clause_mutant,
            caller_vresults=[],
            verdict=CallerSideVerdict.UNVERIFIABLE_BASELINE,
        )

    caller_vresults: list[CallerVerificationResult] = []
    for caller in verifying_callers:
        result = run_cbmc(
            function_to_verify=caller,
            file_containing_function_to_verify=str(mutant_path),
        )
        if result.timed_out:
            caller_vresults.append(
                CallerVerificationResult(
                    caller=caller,
                    returncode=_TIMEOUT_RETURNCODE,
                    successfully_verified=False,
                    timed_out=True,
                )
            )
            continue
        check_expected_cbmc_return_code(result.returncode)
        caller_vresults.append(
            CallerVerificationResult(
                caller=caller,
                returncode=result.returncode,
                successfully_verified=result.is_function_verified,
            )
        )

    # First, check for any non-timeout failures, which takes precedence over timeouts.
    if _is_any_vresult_failure(caller_vresults):
        verdict = CallerSideVerdict.REQUIRED_BY_CALLERS
    elif any(caller_vresult.timed_out for caller_vresult in caller_vresults):
        # Next: check for timeouts.
        verdict = CallerSideVerdict.INDETERMINATE_TIMEOUT
    elif all(caller_vresult.successfully_verified for caller_vresult in caller_vresults):
        verdict = CallerSideVerdict.REDUNDANT_FOR_CALLERS
    else:
        msg = "Unreachable: A verification result is one of success, failure, or timeout."
        raise RuntimeError(msg)
    return ClauseCallerSideResult(clause_mutant, caller_vresults=caller_vresults, verdict=verdict)


def _is_any_vresult_failure(vresults: list[CallerVerificationResult]) -> bool:
    """Return True iff any verification results are a non-timeout failure.

    Args:
        vresults (list[CallerVerificationResult]): The vresults to check for a non-timeout failure.

    Returns:
        bool: True iff any verification results are a non-timeout failure.
    """
    return any(not vresult.timed_out and not vresult.successfully_verified for vresult in vresults)


if __name__ == "__main__":
    sys.exit(main())
