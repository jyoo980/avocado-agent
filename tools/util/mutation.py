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
from tools.run_cbmc import MUTANT_CBMC_TIMEOUT_SEC, CbmcStep, run_cbmc
from tools.util.callgraph import CallGraph
from tools.util.mutation_cache import (
    MutantOutcome,
    MutationCache,
    SkipDecision,
    compute_body_digest,
    compute_mutant_id,
    compute_spec_digest,
    decide_skip,
)
from tools.util.tree_sitter_utils import get_function_body

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
        presumed_equivalent (bool): True iff this mutant was *not* run because it is believed
            unkillable — either the agent declared it equivalent or it survived several distinct
            specifications. Like `timed_out` and `compile_failed` it is undecided, but for a
            different reason, so it is bucketed and reported separately.
        mutant_id (str): The mutant's stable, specification-independent identifier (see
            `tools.util.mutation_cache`). Shown to the client so a specific survivor can be named
            in an `avocado-mark-equivalent` invocation.
        skip_reason (str | None): The `SkipReason` that spared this mutant a CBMC run, or None if
            it was actually verified.
        skip_detail (str): Human-readable justification for the skip, if any.
    """

    mutant: Mutant
    path_to_mutant: str
    killed: bool
    returncode: int
    timed_out: bool = False
    compile_failed: bool = False
    instrumentation_failed: bool = False
    presumed_equivalent: bool = False
    mutant_id: str = ""
    skip_reason: str | None = None
    skip_detail: str = ""

    @property
    def survived(self) -> bool:
        """True iff CBMC decided this mutant and the specification failed to kill it."""
        return not (
            self.killed
            or self.timed_out
            or self.compile_failed
            or self.instrumentation_failed
            or self.presumed_equivalent
        )


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
            Presumed-equivalent mutants are excluded from the denominator, so this is the
            *adjusted* score.
        num_presumed_equivalent (int): The number of mutants skipped as believed-unkillable.
        raw_kill_score (float): killed / (killed + survived + presumed_equivalent) — the score
            with every presumed-equivalent mutant counted as a survivor, i.e. the number this
            function would have reported before any equivalence heuristic was applied. Reported
            alongside `kill_score` so runs stay comparable across the change.
        num_reused_from_cache (int): How many mutants had a remembered verdict replayed instead of
            being re-verified. Reported so the speedup is visible rather than silent.
        spec_digest (str): Digest of the specification these mutants were verified against.
            Recorded in the attempts log so `avocado-verify` can tell "the agent rewrote the
            specification but the score has not moved yet" (progress, keep going) apart from "the
            agent changed nothing" (a plateau, stop).
        results (list[MutantVerificationResult]): The verification result for each mutant.
    """

    num_mutants: int
    num_killed: int
    num_survived: int
    num_timed_out: int
    num_compile_failed: int
    num_instrumentation_failed: int
    kill_score: float
    num_presumed_equivalent: int = 0
    raw_kill_score: float = 0.0
    num_reused_from_cache: int = 0
    spec_digest: str = ""
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
            "instrumentation_failed": self.num_instrumentation_failed,
            "presumed_equivalent": self.num_presumed_equivalent,
            "reused_from_cache": self.num_reused_from_cache,
            "spec_digest": self.spec_digest,
            # Emitted as numbers, not formatted strings: `avocado-verify` reads these back out of
            # the attempts log to decide whether a session actually improved the specification.
            "kill_score": self.kill_score,
            "raw_kill_score": self.raw_kill_score,
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
        case MutationScore() as score:
            return _format_mutation_score_for_client(score)
        case _:
            return str(mutation_testing_result)


def _format_mutation_score_for_client(score: MutationScore) -> str:
    """Render a `MutationScore` as the size-bounded section shown to the agent.

    Args:
        score (MutationScore): The aggregated mutation-testing result.

    Returns:
        str: The formatted section.
    """
    header = _kill_score_lines(score)
    survivors = [vresult for vresult in score.results if vresult.survived]
    equivalents = [vresult for vresult in score.results if vresult.presumed_equivalent]

    trailer = _survivor_trailer(survivors, equivalents)
    if not survivors:
        return f"{header}\nAll decided mutants were killed.{trailer}"

    intro = (
        f"{header}\n{len(survivors)} surviving mutant(s); the spec does not catch these mutants:"
    )
    blocks: list[str] = []
    # The trailer is counted against the budget up front, so the advice at the end of the section
    # can never be what pushes the response over the cap.
    used = len(intro) + len(trailer)
    for index, vresult in enumerate(survivors, start=1):
        mutant = vresult.mutant
        identifier = f" [id {vresult.mutant_id}]" if vresult.mutant_id else ""
        block = (
            f"\n\n--- surviving mutant {index}{identifier} — {score.file}:{mutant.line} "
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

    return intro + "".join(blocks) + trailer


def _kill_score_lines(score: MutationScore) -> str:
    """Return the leading kill-score line(s) for the client section.

    When no mutant was skipped as presumed-equivalent the adjusted and raw scores coincide, so a
    single line is reported in exactly the historical format. Otherwise both scores are shown —
    the adjusted one the agent should optimize, and the raw one that still counts every
    presumed-equivalent mutant as a survivor — so a number here stays comparable with runs
    recorded before any equivalence heuristic existed.

    Args:
        score (MutationScore): The aggregated mutation-testing result.

    Returns:
        str: One or three lines of score reporting, without a trailing newline.
    """
    buckets = (
        f"{score.num_survived} survived, "
        # The undecided buckets are reported too, since they are excluded from the denominator.
        f"{score.num_timed_out} timed out, "
        f"{score.num_compile_failed} compile-failed, "
        f"{score.num_instrumentation_failed} instrumentation-failed"
    )
    if not score.num_presumed_equivalent:
        return (
            f"Mutation kill score: {score.kill_score:.4f} "
            f"(killed {score.num_killed}/{score.num_mutants}; {buckets})"
        )
    return (
        f"Mutation kill score: {score.kill_score:.4f} (adjusted; excludes "
        f"{score.num_presumed_equivalent} presumed-equivalent mutant(s))\n"
        f"Raw kill score: {score.raw_kill_score:.4f} "
        f"(presumed-equivalent mutants counted as survivors)\n"
        f"  killed {score.num_killed}/{score.num_mutants}; {buckets}, "
        f"{score.num_presumed_equivalent} presumed-equivalent"
    )


def _survivor_trailer(
    survivors: list[MutantVerificationResult], equivalents: list[MutantVerificationResult]
) -> str:
    """Return the advice appended after the survivor diffs.

    Args:
        survivors (list[MutantVerificationResult]): The mutants the spec failed to kill.
        equivalents (list[MutantVerificationResult]): The mutants skipped as presumed-equivalent.

    Returns:
        str: The trailing advice, beginning with a newline, or "" when there is nothing to say.
    """
    lines: list[str] = []
    if equivalents:
        declared = sum(1 for vresult in equivalents if vresult.skip_reason == "agent_declared")
        lines.append(
            f"\n{len(equivalents)} mutant(s) were not re-run because they are presumed "
            f"unkillable ({declared} declared by an agent); they are excluded from the adjusted "
            "kill score."
        )
    if survivors:
        lines.append(
            "\nRemember, you MUST try to increase the kill score by strengthening the "
            "specification, but don't keep trying if it is obvious the kill score cannot be "
            "increased. If you are confident a surviving mutant is semantically equivalent to "
            "the original (so no specification could kill it), record that with "
            "`avocado-mark-equivalent --function <F> --file <PATH> --mutant <ID> --reason "
            '"<why>"` instead of continuing to work on it.'
        )
    return "".join(lines)


def generate_mutants_and_compute_score(
    file_path: str,
    target_function: str,
    *,
    include_dirs: list[str] | None = None,
    workspace: Path | None = None,
    keep_artifacts: bool = False,
    skip_reverification: bool = False,
    recheck_equivalent: bool = False,
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
        recheck_equivalent (bool): When True, ignore every remembered verdict and re-verify the
            full mutant set. Use it to audit the equivalence heuristics, which by construction can
            hide a mutant that a later specification would in fact have killed.

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

    total = len(mutants)
    mutant_vresults: list[MutantVerificationResult | None] = [None] * total

    # Consult the persistent cache: a mutant whose verdict cannot have changed (or is believed
    # unkillable) is replayed instead of costing another full CBMC pipeline. `plan` holds the
    # mutants that still have to be run, keyed by their original index so results stay in mutant
    # order and survivor diffs render deterministically.
    cache, cache_context = _load_cache_context(str(source_path), target_function, call_graph)
    plan: dict[int, tuple[Path, Mutant, str]] = {}
    for index, mutant in enumerate(mutants):
        # With the cache disabled there is nothing to key an id against, and an id the agent
        # cannot pass to `avocado-mark-equivalent` is worse than none, so leave it blank.
        mutant_id = "" if cache is None else cache_context.mutant_id_for(mutant)
        decision = (
            None
            if (recheck_equivalent or cache is None)
            else decide_skip(cache_context.entries.get(mutant_id, {}), cache_context.spec_digest)
        )
        if decision is not None:
            mutant_vresults[index] = _result_from_skip(mutant, mutant_id, decision)
            continue
        mutant_path = _get_path_for_mutated_source(workspace, source_path, index)
        plan[index] = (mutant_path, mutant, mutant_id)

    reused = total - len(plan)
    if reused:
        logger.info(
            f"{target_function}: reusing {reused}/{total} remembered mutant verdict(s); "
            f"{len(plan)} to verify"
        )

    # Heads-up to stderr so an agent polling the run can see the slow phase has begun and is
    # making progress. Written to stderr (not stdout) so it never contaminates the kill-score
    # result, and flushed so it is not buffered until the long run completes.
    max_workers = _mutation_worker_count(len(plan))
    if plan:
        print(
            f"Verified {target_function}; now running mutation testing on {len(plan)} of {total} "
            f"mutants ({reused} reused from cache) across up to {max_workers} worker(s) "
            f"(one CBMC run each, up to {MUTANT_CBMC_TIMEOUT_SEC}s per mutant) -- this can take "
            "several minutes; do not interrupt.",
            file=sys.stderr,
            flush=True,
        )
    # Verify mutants concurrently: each mutant is an independent CBMC run, so a bounded thread
    # pool overlaps their (subprocess-bound, GIL-releasing) work. Results are collected as each
    # future completes -- emitting a flushed running tally to stderr so a consumer (a polling
    # agent or a human at the terminal) sees forward motion -- then slotted back into original
    # mutant order. stderr (not stdout) is used so progress never contaminates the result.
    try:
        if plan:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_index = {
                    executor.submit(
                        _verify_mutant, path, mutant, include_dirs, call_graph, mutant_id
                    ): index
                    for index, (path, mutant, mutant_id) in plan.items()
                }
                try:
                    for done, future in enumerate(as_completed(future_to_index), start=1):
                        mutant_vresults[future_to_index[future]] = future.result()
                        # Tally only the mutants this run is actually verifying; replayed verdicts
                        # would otherwise make the running counts disagree with `done`/`total`.
                        _print_mutation_progress(
                            done,
                            len(plan),
                            [
                                vresult
                                for index in plan
                                if (vresult := mutant_vresults[index]) is not None
                            ],
                        )
                finally:
                    # If we exit early (an unexpected error or a Ctrl-C), stop launching mutants
                    # that haven't started yet rather than kicking off more CBMC runs; the `with`
                    # block still waits for any in-flight runs to drain.
                    executor.shutdown(cancel_futures=True)
    finally:
        if not keep_artifacts:
            for path, _, _ in plan.values():
                path.unlink(missing_ok=True)

    # Every future either stored a result above or raised (which would have propagated), so no
    # None slots remain; the filter just refines the type for the aggregator.
    results = [vresult for vresult in mutant_vresults if vresult is not None]
    _remember_verdicts(cache, cache_context, target_function, results)
    return _aggregate_mutation_score(
        results, str(source_path), target_function, cache_context.spec_digest
    )


@dataclass(frozen=True)
class _CacheContext:
    """Everything needed to key this run's mutants against the persistent cache.

    Attributes:
        body_start_byte (int): Byte offset of the target function's body, the origin for the
            specification-independent mutant identity.
        body_digest (str): Digest of the function's body, used to detect edits to the C code.
        spec_digest (str): Digest of the specification the mutants are being verified against.
        entries (dict[str, dict]): The cached per-mutant records for this function.
    """

    body_start_byte: int
    body_digest: str
    spec_digest: str
    entries: dict[str, dict]

    def mutant_id_for(self, mutant: Mutant) -> str:
        """Return `mutant`'s stable identifier under this context.

        Args:
            mutant (Mutant): The mutant to identify.

        Returns:
            str: The mutant's stable identifier.
        """
        return compute_mutant_id(mutant, self.body_start_byte)


def _load_cache_context(
    source_path: str, target_function: str, call_graph: CallGraph
) -> tuple[MutationCache | None, _CacheContext]:
    """Return the cache and the fingerprints keying this run, or a disabled context on failure.

    Caching hinges on locating the function's body, which is what makes mutant identity survive a
    specification edit. If the body cannot be found the cache is disabled for this run rather than
    keyed on something unstable — a missed speedup, never a wrong verdict.

    Args:
        source_path (str): Path to the C source file.
        target_function (str): The function being mutation-tested.
        call_graph (CallGraph): Call graph of the file, used for the specification digest.

    Returns:
        tuple[MutationCache | None, _CacheContext]: The cache (None when disabled) and its context.
    """
    disabled = _CacheContext(body_start_byte=0, body_digest="", spec_digest="", entries={})
    try:
        body = get_function_body(source_path, target_function)
        if body is None:
            logger.debug(f"{target_function}: body not found; mutation cache disabled")
            return None, disabled
        body_digest = compute_body_digest(source_path, target_function)
        spec_digest = compute_spec_digest(source_path, target_function, call_graph)
        cache = MutationCache.load(source_path)
        entries = cache.entries_for(target_function, body_digest)
    # Deliberately broad: the cache is an optimization, so any failure to build its keys must
    # degrade to a full run rather than propagate out of a verification.
    except Exception as error:  # ruff: ignore[blind-except]
        logger.warning(f"{target_function}: mutation cache unavailable ({error}); running in full")
        return None, disabled
    return cache, _CacheContext(
        body_start_byte=body.start_byte,
        body_digest=body_digest,
        spec_digest=spec_digest,
        entries=entries,
    )


def _remember_verdicts(
    cache: MutationCache | None,
    context: _CacheContext,
    target_function: str,
    results: list[MutantVerificationResult],
) -> None:
    """Record this run's freshly-computed verdicts and persist the cache.

    Replayed verdicts are not re-recorded: doing so would let a single unchanged specification
    inflate the distinct-specification counts the equivalence heuristics rely on.

    Args:
        cache (MutationCache | None): The cache, or None when caching is disabled.
        context (_CacheContext): The fingerprints keying this run.
        target_function (str): The function being mutation-tested.
        results (list[MutantVerificationResult]): Every mutant's result for this run.
    """
    if cache is None:
        return
    try:
        for vresult in results:
            if vresult.skip_reason is not None or not vresult.mutant_id:
                continue
            cache.record(
                target_function,
                context.body_digest,
                vresult.mutant_id,
                mutant=vresult.mutant,
                body_offset=vresult.mutant.start_byte - context.body_start_byte,
                spec_digest=context.spec_digest,
                outcome=_outcome_of(vresult),
            )
        cache.save()
    # Deliberately broad: failing to persist verdicts costs future time, never correctness, so it
    # must not turn a completed mutation run into an error.
    except Exception as error:  # ruff: ignore[blind-except]
        logger.warning(f"{target_function}: could not record mutant verdicts ({error})")


def _outcome_of(vresult: MutantVerificationResult) -> MutantOutcome:
    """Return the cacheable outcome corresponding to a freshly-computed result.

    Args:
        vresult (MutantVerificationResult): The result to classify.

    Returns:
        MutantOutcome: The bucket this result falls into.
    """
    if vresult.killed:
        return MutantOutcome.KILLED
    if vresult.timed_out:
        return MutantOutcome.TIMED_OUT
    if vresult.compile_failed:
        return MutantOutcome.COMPILE_FAILED
    if vresult.instrumentation_failed:
        return MutantOutcome.INSTRUMENTATION_FAILED
    return MutantOutcome.SURVIVED


def _result_from_skip(
    mutant: Mutant, mutant_id: str, decision: SkipDecision
) -> MutantVerificationResult:
    """Build the result for a mutant that was skipped rather than re-verified.

    Args:
        mutant (Mutant): The mutant that was skipped.
        mutant_id (str): Its stable identifier.
        decision (SkipDecision): Why it was skipped and which verdict to replay.

    Returns:
        MutantVerificationResult: The replayed result, tagged with the skip reason.
    """
    outcome = decision.outcome
    return MutantVerificationResult(
        mutant=mutant,
        path_to_mutant="",
        killed=outcome is MutantOutcome.KILLED,
        returncode=_VERIFICATION_FAILURE_RETURNCODE if outcome is MutantOutcome.KILLED else 0,
        timed_out=outcome is MutantOutcome.TIMED_OUT,
        compile_failed=outcome is MutantOutcome.COMPILE_FAILED,
        instrumentation_failed=outcome is MutantOutcome.INSTRUMENTATION_FAILED,
        presumed_equivalent=decision.is_presumed_equivalent,
        mutant_id=mutant_id,
        skip_reason=str(decision.reason),
        skip_detail=decision.detail,
    )


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
    survived = sum(1 for r in vresults if r.survived)
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
    spec_digest: str = "",
) -> MutationScore:
    """Aggregate per-mutant results into a MutationScore.

    Timed-out, compile-failed, instrumentation-failed, and presumed-equivalent mutants are bucketed
    separately and excluded from the kill-rate denominator, so `kill_score` reflects only the
    mutants CBMC could decide and that are believed killable in principle. `raw_kill_score` is
    computed alongside it with presumed-equivalent mutants counted as survivors, so the reported
    numbers remain comparable with runs made before the equivalence heuristics existed.

    Args:
        mutant_vresults (list[MutantVerificationResult]): The per-mutant results.
        file (str): The source file containing the function.
        target_function (str): The function under test.
        spec_digest (str): Digest of the specification the mutants were verified against.

    Returns:
        MutationScore: The aggregated score.
    """
    total = len(mutant_vresults)
    killed = sum(1 for r in mutant_vresults if r.killed)
    timed_out = sum(1 for r in mutant_vresults if r.timed_out)
    compile_failed = sum(1 for r in mutant_vresults if r.compile_failed)
    instrumentation_failed = sum(1 for r in mutant_vresults if r.instrumentation_failed)
    presumed_equivalent = sum(1 for r in mutant_vresults if r.presumed_equivalent)
    reused = sum(1 for r in mutant_vresults if r.skip_reason is not None)
    survived = (
        total - killed - timed_out - compile_failed - instrumentation_failed - presumed_equivalent
    )
    decided = killed + survived
    kill_rate = (killed / decided) if decided else 0.0
    # The raw score is what this function would have reported before equivalence was ever
    # inferred: every presumed-equivalent mutant counted as a survivor.
    raw_decided = decided + presumed_equivalent
    raw_kill_rate = (killed / raw_decided) if raw_decided else 0.0
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
        num_presumed_equivalent=presumed_equivalent,
        raw_kill_score=round(raw_kill_rate, 4),
        num_reused_from_cache=reused,
        spec_digest=spec_digest,
        results=mutant_vresults,
    )


def _verify_mutant(
    path_to_write_mutant: Path,
    mutant: Mutant,
    include_dirs: list[str] | None,
    call_graph: CallGraph,
    mutant_id: str = "",
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
        mutant_id (str): The mutant's stable identifier, carried through onto the result so a
            survivor can be named back to the agent.

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
            timeout=MUTANT_CBMC_TIMEOUT_SEC,
            # Each mutant would otherwise append to its own `<mutant>-cbmc-runs.jsonl` beside the
            # source, which the mutant `.c` cleanup does not remove.
            log_invocation=False,
        )
    if cbmc_result.timed_out:
        return MutantVerificationResult(
            mutant,
            path_to_mutant=str(path_to_write_mutant),
            killed=False,
            returncode=cbmc_result.returncode,
            timed_out=True,
            mutant_id=mutant_id,
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
                mutant_id=mutant_id,
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
            mutant_id=mutant_id,
        )

    check_expected_cbmc_return_code(cbmc_result.returncode)
    return MutantVerificationResult(
        mutant,
        path_to_mutant=str(path_to_write_mutant),
        killed=cbmc_result.returncode == _VERIFICATION_FAILURE_RETURNCODE,
        returncode=cbmc_result.returncode,
        mutant_id=mutant_id,
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
