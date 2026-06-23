"""Mutation-testing orchestration for CBMC-annotated C functions.

Given a function with a CBMC contract, generate body mutants (operator swaps via
`eval.mutants.mutate_function.get_mutants`), run CBMC on each, and report what
fraction the spec "kills" along with any surviving mutants.
A mutant is killed iff CBMC fails on it; a surviving mutant indicates the spec is too weak to catch
that perturbation.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

from eval.mutants.mutate_function import Mutant, get_mutants
from eval.mutants.util import (
    check_expected_cbmc_return_code,
    is_valid_mutation_candidate,
)
from tools.construct_call_graph import construct_call_graph
from tools.run_cbmc import CbmcStep, run_cbmc
from tools.util.callgraph import CallGraph

# Matches the GNU `timeout(1)` convention used elsewhere in the codebase; surfaces in
# MutantVerificationResult.returncode so consumers can distinguish a timed-out run from a
# real CBMC failure (10) or success (0).
_VERIFICATION_FAILURE_RETURNCODE = 10

# Character budget for the mutation-testing section appended to a *success* response.
# Surviving-mutant diff volume is unbounded in principle, so cap it the way failure
# output is capped, dropping trailing survivors behind an explicit omission marker.
_MAX_MUTATION_SECTION_CHARS = 50_000

# Upper bound on how many mutants are verified concurrently. Each worker drives a full CBMC
# pipeline (its own subprocesses), which can be memory-heavy, so the effective worker count is
# min(this cap, os.cpu_count(), number of mutants). The cap bounds peak memory on machines with
# many cores; lower it if mutation runs exhaust RAM.
_MAX_MUTATION_WORKERS = 32


@dataclass(frozen=True)
class MutantVerificationResult:
    """Verification result for a single mutant.

    Attributes:
        mutant (Mutant): The mutant that was verified.
        path_to_mutant (str): The path to the file in which the mutant is declared.
        killed (bool): True iff this mutant was killed.
        returncode (int): The return code of the CBMC (or goto-cc) process used to verify this
            mutant. For timed-out runs this is the timeout sentinel (124), not a real CBMC exit
            code. For compile-failed runs this is goto-cc's exit code.
        timed_out (bool): True iff CBMC exceeded its per-attempt timeout for this mutant.
            A timed-out mutant is neither killed nor survived — it is undecided.
        compile_failed (bool): True iff goto-cc rejected the mutant source (e.g., the mutation
            produced invalid C such as adding two pointers). Like `timed_out`, a compile-failed
            mutant is undecided: CBMC never ran, so the spec's strength is not evidenced.
        instrumentation_failed (bool): True iff any of the goto-instrument steps failed.
            These failures are not necessarily indicative of errors with the specification.
    """

    mutant: Mutant
    path_to_mutant: str
    killed: bool
    returncode: int
    timed_out: bool = False
    compile_failed: bool = False
    instrumentation_failed: bool = False


@dataclass(frozen=True)
class MutationTestingResult:
    """Base class representing a mutation testing result."""

    file: str
    target_function: str


@dataclass(frozen=True)
class MutationScore(MutationTestingResult):
    """Mutation-testing related statistics for one function.

    `num_survived` and `kill_score` are reported over *decided* mutants only —
    timed-out and compile-failed mutants are excluded from both the survivor count
    and the kill-rate denominator. `num_timed_out` and `num_compile_failed` are
    reported separately so consumers can see how much of the mutant space was
    undecidable and how much produced invalid C in the first place.

    Note: A function can have 0 mutants (e.g., a function that solely calls another function
    will not have any mutants generated).

    Attributes
    ----------
        num_mutants (int): The total number of mutants for this function.
        num_killed (int): The number of killed mutants.
        num_survived (int): The number of surviving (decided, not killed) mutants.
        num_timed_out (int): The number of mutants for which CBMC exceeded its timeout.
        num_compile_failed (int): The number of mutants that goto-cc rejected as invalid C.
        num_instrumentation_failed (int): The number of mutants that goto-instrument failed on.
        kill_score (float): killed / (killed + survived); 0.0 when no mutants were decided.
        results (list[MutantVerificationResult]): The verification result for each mutant.
    """

    num_mutants: int
    num_killed: int
    num_survived: int
    num_timed_out: int
    num_compile_failed: int
    num_instrumentation_failed: int
    kill_score: float
    results: list[MutantVerificationResult] = field(default_factory=list)

    def summary(self) -> dict[str, str | int | float]:
        """Return a summary of this mutation score.

        Returns:
            dict[str, str | int | float]: A summary of this mutation score.
        """
        return {
            "kind": "mutation_summary",
            "was_mutation_tested": True,
            "file": self.file,
            "function": self.target_function,
            "total": self.num_mutants,
            "killed": self.num_killed,
            "survived": self.num_survived,
            "timed_out": self.num_timed_out,
            "compile_failed": self.num_compile_failed,
            "instrumentation_failed": self.num_compile_failed,
            "kill_score": f"{self.kill_score:.4f}",
        }


@dataclass(frozen=True)
class NoMutantsGenerated(MutationTestingResult):
    """Represent a mutation testing result when a function has no mutants."""

    def __str__(self) -> str:
        """Return the string for a mutation testing result for a function with no mutants.

        Returns:
            str: Return the string for a mutation testing result for a function with no mutants.
        """
        return (
            f"Mutation testing not possible for '{self.file}#{self.target_function}'; "
            "no mutable operators"
        )


@dataclass(frozen=True)
class BaselineFailsVerification(MutationTestingResult):
    """Represent a mutation testing result when a function fails to verify in the first place."""

    def __str__(self) -> str:
        """Return the string for a mutation testing result for a non-verifying function.

        Returns:
            str: Return the string for a mutation testing result for a non-verifying function.
        """
        return f"{self.file}#{self.target_function} did not verify; cannot score mutants"


def get_mutation_testing_results_for_client(mutation_testing_result: MutationTestingResult) -> str:
    """Return mutation-testing information that can be used by a client.

    Returns a string comprising a summary header followed by the unified diff(s) of each surviving
    mutant and the original source. Diffs are emitted verbatim rather than JSON-escaped so the agent
    can read them directly. The section is bounded by `_MAX_MUTATION_SECTION_CHARS`: once appending
    the next survivor's block would exceed the budget, the remaining survivors are dropped behind an
    explicit omission marker.

    Args:
        mutation_testing_result (MutationTestingResult): The mutation testing result for the
            verified function.

    Returns:
        str: The formatted, size-bounded mutation-testing section.
    """
    match mutation_testing_result:
        case MutationScore(
            file,
            _,
            num_mutants,
            num_killed,
            num_survived,
            num_timed_out,
            num_compile_failed,
            num_instrumentation_failed,
            kill_score,
            results,
        ):
            kill_score_line = (
                f"Mutation kill score: {kill_score:.4f} "
                f"(killed {num_killed}/{num_mutants}; "
                f"{num_survived} survived, "
                # The values for the number of timed-out/compile/instrumentation-failed mutants are
                # also reported since the denominator for the kill score includes them.
                f"{num_timed_out} timed out, "
                f"{num_compile_failed} compile-failed, "
                f"{num_instrumentation_failed} instrumentation-failed)"
            )
            # A surviving mutant is one that compiled and was decided (not timed out),
            # yet the spec did not kill.
            survivors = [
                vresult
                for vresult in results
                if not (
                    vresult.killed
                    or vresult.compile_failed
                    or vresult.timed_out
                    or vresult.instrumentation_failed
                )
            ]
            if not survivors:
                return f"{kill_score_line}\nAll decided mutants were killed."

            header = (
                f"{kill_score_line}\n"
                f"{len(survivors)} surviving mutant(s); "
                "the spec does not catch these mutants:"
            )
            blocks: list[str] = []
            used = len(header)
            for index, vresult in enumerate(survivors, start=1):
                mutant = vresult.mutant
                block = (
                    f"\n\n--- surviving mutant {index} — {file}:{mutant.line} "
                    f"({mutant.operator_class}: "
                    f"{mutant.original_operator} -> {mutant.replacement_operator}) ---\n"
                    f"{mutant.get_unified_diff()}"
                )
                if used + len(block) > _MAX_MUTATION_SECTION_CHARS:
                    omitted = len(survivors) - index + 1
                    blocks.append(f"\n\n[... {omitted} more surviving mutant(s) omitted ...]")
                    break
                blocks.append(block)
                used += len(block)

            return (
                header
                + "".join(blocks)
                + "\nRemember, you MUST try to increase the kill score by strengthening the "
                + "specification, but don't keep trying if it is obvious the kill score cannot be "
                + "increased."
            )
        case _:
            return str(mutation_testing_result)


def generate_mutants_and_compute_score(
    file_path: str,
    target_function: str,
    *,
    include_dirs: list[str] | None = None,
    workspace: Path | None = None,
    keep_artifacts: bool = False,
    skip_reverification: bool = False,
) -> MutationTestingResult:
    """Return the mutation testing result for `target_function` in `file_path`.

    Mutant `.c` files are written next to the original source by default to simplify compilation
    and instrumentation with CBMC. Mutants are removed unless keep_artifacts is set to `True`.

    Args:
        file_path (str): Path to the C source defining the function.
        target_function (str): The function for which to generate mutants.
        include_dirs (list[str] | None): Directories forwarded to `run_cbmc()` as `-I` flags
            for both the original-function verification and every mutant run.
        workspace (Path | None): Directory to write mutant files into. Defaults to the
            source file's directory.
        keep_artifacts (bool): When True, mutant `.c` files are kept for inspection.
        skip_reverification (bool): When True, proceed with mutation testing regardless of whether
            the function verifies or not.

    Returns:
        MutationTestingResult: The result of running mutation testing on the target function.
    """
    source_path = Path(file_path).resolve()
    mutants = get_mutants(str(source_path), target_function)
    if not mutants:
        return NoMutantsGenerated(file_path, target_function)

    workspace = workspace or source_path.parent
    workspace.mkdir(parents=True, exist_ok=True)

    if not skip_reverification:
        cbmc_result = run_cbmc(target_function, file_path, include_dirs=include_dirs)
        if not is_valid_mutation_candidate(cbmc_result):
            # No usable baseline if CBMC can't verify the unmutated function.
            return BaselineFailsVerification(file_path, target_function)

    # Operator-swap mutations never add or remove calls (or rename functions), so the call graph
    # is identical for the original and every mutant. Build it once -- cache-hitting the JSON the
    # baseline run above already wrote -- and pass it into each mutant's run_cbmc below. Besides
    # avoiding redundant parsing, this keeps the non-thread-safe tree-sitter parser out of the
    # concurrent section: no call graph is constructed inside the worker threads.
    call_graph = CallGraph(
        json.loads(Path(construct_call_graph(file_path)).read_text(encoding="utf-8"))
    )

    paths_to_mutants = {
        _get_path_for_mutated_source(workspace, source_path, i): mutant
        for i, mutant in enumerate(mutants)
    }
    # Heads-up to stderr so an agent polling the run can see the slow phase has begun and is
    # making progress. Written to stderr (not stdout) so it never contaminates the kill-score
    # result, and flushed so it is not buffered until the long run completes.
    total = len(paths_to_mutants)
    max_workers = _mutation_worker_count(total)
    if mutants:
        print(
            f"Verified {target_function}; now running mutation testing on {total} "
            f"mutants across up to {max_workers} worker(s) (one CBMC run each, up to 10 min "
            "per mutant) -- this can take several minutes; do not interrupt.",
            file=sys.stderr,
            flush=True,
        )
    # Verify mutants concurrently: each mutant is an independent CBMC run, so a bounded thread
    # pool overlaps their (subprocess-bound, GIL-releasing) work. Results are collected as each
    # future completes -- emitting a flushed running tally to stderr so a consumer (a polling
    # agent or a human at the terminal) sees forward motion -- then slotted back into original
    # mutant order so survivor diffs render deterministically downstream. stderr (not stdout) is
    # used so progress never contaminates the kill-score result.
    mutant_vresults: list[MutantVerificationResult | None] = [None] * total
    try:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_index = {
                executor.submit(_verify_mutant, path, mutant, include_dirs, call_graph): index
                for index, (path, mutant) in enumerate(paths_to_mutants.items())
            }
            try:
                for done, future in enumerate(as_completed(future_to_index), start=1):
                    mutant_vresults[future_to_index[future]] = future.result()
                    _print_mutation_progress(
                        done, total, [r for r in mutant_vresults if r is not None]
                    )
            finally:
                # If we exit early (an unexpected error or a Ctrl-C), stop launching mutants that
                # haven't started yet rather than kicking off more 10-minute CBMC runs; the `with`
                # block still waits for any in-flight runs to drain.
                executor.shutdown(cancel_futures=True)
    finally:
        if not keep_artifacts:
            for path in paths_to_mutants:
                path.unlink(missing_ok=True)

    # Every future either stored a result above or raised (which would have propagated), so no
    # None slots remain; the filter just refines the type for the aggregator.
    results = [vresult for vresult in mutant_vresults if vresult is not None]
    return _aggregate_mutation_score(results, str(source_path), target_function)


def _mutation_worker_count(num_mutants: int) -> int:
    """Return how many mutants to verify concurrently.

    Bounded by `_MAX_MUTATION_WORKERS`, the machine's CPU count, and the number of mutants, and
    never below 1 (a `ThreadPoolExecutor` requires a positive worker count, even when there are
    no mutants to run).

    Args:
        num_mutants (int): The total number of mutants to verify.

    Returns:
        int: The number of worker threads to use.
    """
    return max(1, min(num_mutants, os.cpu_count() or 1, _MAX_MUTATION_WORKERS))


def _print_mutation_progress(
    done: int, total: int, vresults: list[MutantVerificationResult]
) -> None:
    """Print a one-line, flushed mutation-testing progress update to stderr.

    Reports the running tally over the mutants decided so far. The buckets mirror those in
    `_aggregate_mutation_score` so the live counts agree with the final `MutationScore`.

    Args:
        done (int): The number of mutants verified so far.
        total (int): The total number of mutants.
        vresults (list[MutantVerificationResult]): The per-mutant results accumulated so far.
    """
    killed = sum(1 for r in vresults if r.killed)
    timed_out = sum(1 for r in vresults if r.timed_out)
    compile_failed = sum(1 for r in vresults if r.compile_failed)
    instrumentation_failed = sum(1 for r in vresults if r.instrumentation_failed)
    survived = len(vresults) - killed - timed_out - compile_failed - instrumentation_failed
    print(
        f"[mutation testing] {done}/{total} done "
        f"(killed={killed} survived={survived} timed_out={timed_out} "
        f"compile_failed={compile_failed} instrumentation_failed={instrumentation_failed})",
        file=sys.stderr,
        flush=True,
    )


def _aggregate_mutation_score(
    mutant_vresults: list[MutantVerificationResult],
    file: str,
    target_function: str,
) -> MutationScore:
    """Aggregate per-mutant results into a MutationScore.

    Timed-out, compile-failed, and instrumentation-failed mutants are bucketed separately and
    excluded from the kill-rate denominator, so `kill_score` reflects only the mutants CBMC could
    decide.

    Args:
        mutant_vresults (list[MutantVerificationResult]): The per-mutant results.
        file (str): The source file containing the function.
        target_function (str): The function under test.

    Returns:
        MutationScore: The aggregated score.
    """
    total = len(mutant_vresults)
    killed = sum(1 for r in mutant_vresults if r.killed)
    timed_out = sum(1 for r in mutant_vresults if r.timed_out)
    compile_failed = sum(1 for r in mutant_vresults if r.compile_failed)
    instrumentation_failed = sum(1 for r in mutant_vresults if r.instrumentation_failed)
    survived = total - killed - timed_out - compile_failed - instrumentation_failed
    decided = killed + survived
    kill_rate = (killed / decided) if decided else 0.0
    return MutationScore(
        file=file,
        target_function=target_function,
        num_mutants=total,
        num_killed=killed,
        num_survived=survived,
        num_timed_out=timed_out,
        num_compile_failed=compile_failed,
        num_instrumentation_failed=instrumentation_failed,
        kill_score=round(kill_rate, 4),
        results=mutant_vresults,
    )


def _verify_mutant(
    path_to_write_mutant: Path,
    mutant: Mutant,
    include_dirs: list[str] | None,
    call_graph: CallGraph,
) -> MutantVerificationResult:
    """Return the result of verifying a mutant.

    Mutants that goto-cc rejects (uncompilable C) are short-circuited before CBMC runs and
    returned with `compile_failed=True`; this prevents `check_expected_cbmc_return_code` from
    raising on goto-cc's exit code and lets callers exclude these mutants from the kill rate.

    Safe to call concurrently for sibling mutants of one function: the CBMC pipeline runs in a
    private scratch directory so its function-name-derived `.goto` intermediates never collide,
    and the shared `call_graph` is read-only.

    Args:
        path_to_write_mutant (Path): The path to which the mutated source is written.
        mutant (Mutant): The mutant.
        include_dirs (list[str] | None): Include directories, which are forwarded to `run_cbmc()`.
        call_graph (CallGraph): The original function's call graph, reused verbatim for this
            mutant (operator-swap mutants share the original's call graph) and passed to
            `run_cbmc()` so it skips re-parsing the mutant source.

    Returns:
        MutantVerificationResult: The result of verifying a mutant. The returned result's
            `compile_failed` is True iff goto-cc rejected the source, in which case CBMC was
            not run.
    """
    path_to_write_mutant.write_text(mutant.mutant_source, encoding="utf-8")
    # The CBMC pipeline writes intermediate `<function>.goto` / `checking-<function>-contracts.goto`
    # files named only by the function name -- identical across all mutants of one function -- into
    # its working directory. Give each run a private scratch dir so concurrent mutants don't clobber
    # one another's goto-binaries (this also keeps the source directory clean). The result is read
    # entirely from the in-memory `cbmc_result`, so the scratch dir can be torn down immediately.
    with tempfile.TemporaryDirectory(prefix="avocado-mutant-") as scratch_dir:
        cbmc_result = run_cbmc(
            function_to_verify=mutant.function,
            file_containing_function_to_verify=str(path_to_write_mutant),
            include_dirs=include_dirs,
            call_graph=call_graph,
            cwd=scratch_dir,
        )
    if cbmc_result.timed_out:
        return MutantVerificationResult(
            mutant,
            path_to_mutant=str(path_to_write_mutant),
            killed=False,
            returncode=cbmc_result.returncode,
            timed_out=True,
        )
    if failed_step := cbmc_result.failed_step:
        if failed_step == CbmcStep.CBMC:
            # The `cbmc` command itself could fail with an error unrelated to verification.
            # Check here for that case.
            check_expected_cbmc_return_code(cbmc_result.returncode)
            return MutantVerificationResult(
                mutant,
                path_to_mutant=str(path_to_write_mutant),
                killed=cbmc_result.returncode == _VERIFICATION_FAILURE_RETURNCODE,
                returncode=cbmc_result.returncode,
            )
        compile_failed = cbmc_result.failed_step == CbmcStep.GOTO_CC
        if compile_failed:
            logger.warning(
                f"mutant failed to compile: {mutant.function} at "
                f"{path_to_write_mutant}:{mutant.line}:{mutant.column} "
                f"({mutant.operator_class}: {mutant.original_operator} -> "
                f"{mutant.replacement_operator}); goto-cc returncode={cbmc_result.returncode}"
            )
        return MutantVerificationResult(
            mutant,
            path_to_mutant=str(path_to_write_mutant),
            killed=False,
            returncode=cbmc_result.returncode,
            compile_failed=compile_failed,
            instrumentation_failed=cbmc_result.failed_step == CbmcStep.GOTO_INSTRUMENT,
        )

    check_expected_cbmc_return_code(cbmc_result.returncode)
    return MutantVerificationResult(
        mutant,
        path_to_mutant=str(path_to_write_mutant),
        killed=cbmc_result.returncode == _VERIFICATION_FAILURE_RETURNCODE,
        returncode=cbmc_result.returncode,
    )


def _get_path_for_mutated_source(
    workspace_path: Path, path_to_original_source: Path, index: int
) -> Path:
    """Return the path to which to write a mutated source file.

    For example, given the path `/app/test/data/foo.c`, return `/app/test/data/foo__mutant_1.c`

    Args:
        workspace_path (Path): The directory under which mutation testing occurs.
        path_to_original_source (Path): The path to the original source file.
        index (int): The index of the mutant, used as a identifier for the mutant source path.

    Returns:
        Path: The path to which to write a mutated source file.
    """
    return (
        workspace_path
        / f"{path_to_original_source.stem}__mutant_{index}{path_to_original_source.suffix}"
    )
