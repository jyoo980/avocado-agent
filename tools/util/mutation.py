"""Mutation-testing orchestration for CBMC-annotated C functions.

Given a function with a CBMC contract, generate body mutants (operator swaps via
`eval.mutants.mutate_function.get_mutants`), run CBMC on each, and report what
fraction the spec "kills" along with any surviving mutants.
A mutant is killed iff CBMC fails on it; a surviving mutant indicates the spec is too weak to catch
that perturbation.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

from eval.mutants.mutate_function import Mutant, get_mutants
from eval.mutants.util import (
    check_expected_cbmc_return_code,
    is_valid_mutation_candidate,
)
from tools.run_cbmc import CbmcStep, run_cbmc

# Matches the GNU `timeout(1)` convention used elsewhere in the codebase; surfaces in
# MutantVerificationResult.returncode so consumers can distinguish a timed-out run from a
# real CBMC failure (10) or success (0).
_VERIFICATION_FAILURE_RETURNCODE = 10

# Character budget for the mutation-testing section appended to a *success* response.
# Surviving-mutant diff volume is unbounded in principle, so cap it the way failure
# output is capped, dropping trailing survivors behind an explicit omission marker.
_MAX_MUTATION_SECTION_CHARS = 50_000


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
class MutationScore:
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
        file (str): The file in which the original function is declared.
        target_function (str): The name of this function.
        num_mutants (int): The total number of mutants for this function.
        num_killed (int): The number of killed mutants.
        num_survived (int): The number of surviving (decided, not killed) mutants.
        num_timed_out (int): The number of mutants for which CBMC exceeded its timeout.
        num_compile_failed (int): The number of mutants that goto-cc rejected as invalid C.
        num_instrumentation_failed (int): The number of mutants that goto-instrument failed on.
        kill_score (float): killed / (killed + survived); 0.0 when no mutants were decided.
        results (list[MutantVerificationResult]): The verification result for each mutant.
    """

    file: str
    target_function: str
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


def get_mutation_testing_results_for_client(mutation_score: MutationScore) -> str:
    """Return mutation-testing information that can be used by a client.

    Returns a string comprising a summary header followed by the unified diff(s) of each surviving
    mutant and the original source. Diffs are emitted verbatim rather than JSON-escaped so the agent
    can read them directly. The section is bounded by `_MAX_MUTATION_SECTION_CHARS`: once appending
    the next survivor's block would exceed the budget, the remaining survivors are dropped behind an
    explicit omission marker.

    Args:
        mutation_score (MutationScore): The mutation score for the verified function.

    Returns:
        str: The formatted, size-bounded mutation-testing section.
    """
    if not mutation_score.num_mutants:
        return (
            f"No mutants generated for '{mutation_score.target_function}' "
            "(no mutable operators in the function body)\n"
        )
    kill_score_line = (
        f"Mutation kill score: {mutation_score.kill_score:.4f} "
        f"(killed {mutation_score.num_killed}/{mutation_score.num_mutants}; "
        f"{mutation_score.num_survived} survived, "
        # The values for the number of timed-out/compile-failed mutants are also reported since
        # the denominator for the kill score includes them.
        f"{mutation_score.num_timed_out} timed out, "
        f"{mutation_score.num_compile_failed} compile-failed)"
    )
    # A surviving mutant is one that compiled and was decided (not timed out) yet the spec
    # did not kill — mirrors `_is_valid_mutation_vresult` in tools/get_mutation_score.py.
    survivors = [
        vresult
        for vresult in mutation_score.results
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
        f"{len(survivors)} surviving mutant(s) — the spec does not catch these perturbations:"
    )
    blocks: list[str] = []
    used = len(header)
    for index, vresult in enumerate(survivors, start=1):
        mutant = vresult.mutant
        block = (
            f"\n\n--- surviving mutant {index} — {mutation_score.file}:{mutant.line} "
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
        + "\nRemember, you MUST try to increase the kill score by strengthening the specification, "
        + "but don't keep trying if it is obvious the kill score cannot be increased."
    )


def generate_mutants_and_compute_score(
    file_path: str,
    target_function: str,
    *,
    include_dirs: list[str] | None = None,
    workspace: Path | None = None,
    keep_artifacts: bool = False,
) -> MutationScore | None:
    """Return the mutation kill score for `target_function` in `file_path`.

    Mutant `.c` files are written next to the original source by default to simplify compilation
    and instrumentation with CBMC. Mutants are removed unless keep_artifacts is set to `True`.

    This function returns None if the original, unmutated function does not verify in the first
    place.

    Args:
        file_path (str): Path to the C source defining the function.
        target_function (str): The function for which to generate mutants.
        include_dirs (list[str] | None): Directories forwarded to `run_cbmc()` as `-I` flags
            for both the original-function verification and every mutant run.
        workspace (Path | None): Directory to write mutant files into. Defaults to the
            source file's directory.
        keep_artifacts (bool): When True, mutant `.c` files are kept for inspection.

    Returns:
        MutationScore | None: Aggregated counts plus per-mutant verification results, or None if
            the unmutated function does not verify.
    """
    source_path = Path(file_path).resolve()
    workspace = workspace or source_path.parent
    workspace.mkdir(parents=True, exist_ok=True)

    cbmc_result = run_cbmc(target_function, file_path, include_dirs=include_dirs)
    if not is_valid_mutation_candidate(cbmc_result):
        # No usable baseline if CBMC can't verify the unmutated function.
        logger.warning(f"could not verify {target_function}; skipping mutation testing")
        return None

    mutants = get_mutants(str(source_path), target_function)
    paths_to_mutants = {
        _get_path_for_mutated_source(workspace, source_path, i): mutant
        for i, mutant in enumerate(mutants)
    }
    # Heads-up to stderr so an agent polling the run can see the slow phase has begun and is
    # making progress. Written to stderr (not stdout) so it never contaminates the kill-score
    # result, and flushed so it is not buffered until the long run completes.
    if mutants:
        print(
            f"Verified {target_function}; now running mutation testing on {len(mutants)} "
            "mutants (one CBMC run each, up to 10 min per mutant) -- this can take several "
            "minutes; do not interrupt.",
            file=sys.stderr,
            flush=True,
        )
    # Verify mutants one at a time, emitting per-mutant progress to stderr so a consumer
    # (an agent polling the run, or a human at the terminal) can see forward motion. Each
    # mutant is a full CBMC run that can take up to 10 minutes, so a "starting" line is
    # printed before each run and a "done" line with the running tally after it. stderr is
    # used (not stdout) so progress never contaminates the kill-score result, and each line
    # is flushed so it appears immediately rather than at the end of the long run.
    total = len(paths_to_mutants)
    mutant_vresults: list[MutantVerificationResult] = []
    try:
        for i, (path, mutant) in enumerate(paths_to_mutants.items(), start=1):
            print(
                f"[mutation testing] starting mutant {i}/{total}...",
                file=sys.stderr,
                flush=True,
            )
            mutant_vresults.append(_verify_mutant(path, mutant, include_dirs))
            _print_mutation_progress(i, total, mutant_vresults)
    finally:
        if not keep_artifacts:
            for path in paths_to_mutants:
                path.unlink(missing_ok=True)

    return _aggregate_mutation_score(mutant_vresults, str(source_path), target_function)


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
) -> MutantVerificationResult:
    """Return the result of verifying a mutant.

    Mutants that goto-cc rejects (uncompilable C) are short-circuited before CBMC runs and
    returned with `compile_failed=True`; this prevents `check_expected_cbmc_return_code` from
    raising on goto-cc's exit code and lets callers exclude these mutants from the kill rate.

    Args:
        path_to_write_mutant (Path): The path to which the mutated source is written.
        mutant (Mutant): The mutant.
        include_dirs (list[str] | None): Include directories, which are forwarded to `run_cbmc()`.

    Returns:
        MutantVerificationResult: The result of verifying a mutant. The returned result's
            `compile_failed` is True iff goto-cc rejected the source, in which case CBMC was
            not run.
    """
    path_to_write_mutant.write_text(mutant.mutant_source, encoding="utf-8")
    cbmc_result = run_cbmc(
        function_to_verify=mutant.function,
        file_containing_function_to_verify=str(path_to_write_mutant),
        include_dirs=include_dirs,
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
