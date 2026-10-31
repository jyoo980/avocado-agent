"""Decide whether a function that verifies does so vacuously.

A contract is checked by assuming its preconditions, running the body, and asserting its
postconditions wherever the function returns. If no execution CBMC explores gets that far, there
is nothing to assert and the run passes whatever the postconditions say. That happens when the
preconditions contradict one another, when a callee's contract promises something impossible, or
when the harness's loop and depth bounds cut every path short. Such a pass proves nothing, and
every mutant of the function survives it.

The check is one extra run of the pipeline on a copy of the file with two probes added to the
function: a postcondition that is false (`__CPROVER_ensures(0)`) and an assertion that is false as
the first statement of the body. A false assertion *fails* exactly when an execution reaches it, so:

- neither probe fails: no execution enters the body;
- only the entry probe fails: executions enter the body but none reaches the end;
- both fail: the function is entered and left, so its postconditions were really checked.

When no execution enters the body, either the preconditions cannot all hold, or the steps taken
before the body use up CBMC's `--depth` bound. A second extra run tells the two apart: on a copy
whose body starts with the false assertion followed by `__CPROVER_assume(0)`, CBMC runs without
the depth bound. Every path ends at the top of the body, so the run stays short. If the assertion
fails, the preconditions can hold and the depth bound was what kept executions out of the body
(`DEPTH_LIMIT_REACHED`). That is not counted as vacuous: like an `UNKNOWN` result, the pass stands.

The copies live in a temporary directory and are never seen by a session or by a merge.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from tools.run_cbmc import (
    _DEFAULT_RUN_CBMC_TIMEOUT_SEC,
    CbmcStep,
    PipelineVariant,
    RunCbmcResult,
    _run_cbmc_without_depth_bound,
    run_cbmc,
)
from tools.util.edit_rules import function_body_offsets
from tools.util.vacuity_check_result import VACUOUS_KINDS, Vacuity, VacuityCheckResult

# The message of the assertion added as the first statement of the body. CBMC prints it on the
# line that reports the assertion, which is how the two probes are told apart.
_ENTRY_PROBE = "AVOCADO_VACUITY_PROBE_BODY_ENTERED"

_TEMP_DIR_PREFIX = "avocado-vacuity-"

# Per-step time limit of the run without the depth bound. Its paths end at the top of the body, so
# it only has to get through what comes before; a run that takes longer gives no answer.
_ENTRY_ONLY_PROBE_TIMEOUT_SEC = 120


def check_vacuity(
    function: str,
    file_path: str,
    include_dirs: list[str] | None = None,
    *,
    variant: PipelineVariant | None = None,
    depth: int,
) -> VacuityCheckResult:
    """Return whether `function`, which verifies in `file_path`, does so vacuously.

    The probed copy is run through the same variant of the pipeline as the run that verified,
    at the same depth, and only that one. The probed run fails by design; left to itself,
    `run_cbmc` could take that failure as a reason to retry differently, and the verdict would
    then be about a pipeline other than the one whose pass is being checked. A different depth
    would do the same: whether an execution reaches the end of the function depends on the bound.

    Args:
        function (str): A function that verifies.
        file_path (str): Path to the C file defining it.
        include_dirs (list[str] | None): Directories forwarded to `goto-cc` as `-I` flags.
        variant (PipelineVariant | None): The variant of the pipeline in which the function
            verified (`RunCbmcResult.variant`); None means the plain pipeline.
        depth (int): The `--depth` at which the function verified (`RunCbmcResult.depth`).

    Returns:
        VacuityCheckResult: The result of checking the function specification for vacuity.
    """
    source_path = Path(file_path).resolve()
    try:
        source = source_path.read_bytes()
    except OSError as error:
        return VacuityCheckResult(Vacuity.UNKNOWN, f"could not read {source_path}: {error}")
    probed = add_probes(source, function)
    if probed is None:
        return VacuityCheckResult(
            Vacuity.UNKNOWN, f"no definition of {function} was found to probe"
        )
    variant = variant or PipelineVariant()
    vacuity_check_result = classify_probe_run(
        _run_probed_copy(function, probed, source_path, include_dirs, variant=variant, depth=depth)
    )
    if vacuity_check_result.vacuity is not Vacuity.UNSATISFIABLE_PRECONDITIONS:
        return vacuity_check_result
    # No execution entered the body: find out whether the depth bound is why.
    entry_probed = add_entry_probe(source, function)
    assert entry_probed is not None  # The function was found above.
    entry_run = _run_probed_copy(
        function,
        entry_probed,
        source_path,
        include_dirs,
        variant=variant,
        depth=depth,
        without_depth_bound=True,
        timeout_sec=_ENTRY_ONLY_PROBE_TIMEOUT_SEC,
    )
    return classify_entry_probe_run(entry_run)


def _run_probed_copy(
    function: str,
    probed: bytes,
    source_path: Path,
    include_dirs: list[str] | None,
    *,
    variant: PipelineVariant,
    depth: int,
    without_depth_bound: bool = False,
    timeout_sec: int = _DEFAULT_RUN_CBMC_TIMEOUT_SEC,
) -> RunCbmcResult:
    """Run the pipeline once, in `variant`, on a probed copy of the file in a scratch directory.

    Args:
        function (str): The probed function.
        probed (bytes): The probed source.
        source_path (Path): Absolute path to the original file. Its directory is put on the
            include path, since the copy is alone in its own.
        include_dirs (list[str] | None): Further include directories.
        variant (PipelineVariant): The variant of the pipeline to run.
        depth (int): The `--depth` of the run.
        without_depth_bound (bool): When True, the pipeline's binary is then checked by CBMC with
            no `--depth` (`_run_cbmc_without_depth_bound`).
        timeout_sec (int): Per-step time limit, in seconds.

    Returns:
        RunCbmcResult: The outcome of the run.
    """
    with tempfile.TemporaryDirectory(prefix=_TEMP_DIR_PREFIX) as scratch:
        probe_dir = Path(scratch) / "source"
        work_dir = Path(scratch) / "work"
        probe_dir.mkdir()
        work_dir.mkdir()
        probe_file = probe_dir / source_path.name
        probe_file.write_bytes(probed)
        # The copy is alone in its directory, so the headers beside the original are found
        # through the include path instead.
        probe_include_dirs = [
            str(source_path.parent),
            *(str(Path(directory).resolve()) for directory in include_dirs or []),
        ]
        if without_depth_bound:
            return _run_cbmc_without_depth_bound(
                function,
                str(probe_file),
                include_dirs=probe_include_dirs,
                cwd=str(work_dir),
                timeout_sec=timeout_sec,
                variant=variant,
                depth=depth,
            )
        return run_cbmc(
            function_to_verify=function,
            file_containing_function_to_verify=str(probe_file),
            include_dirs=probe_include_dirs,
            timeout_sec=timeout_sec,
            variant=variant,
            depth=depth,
        )


def add_probes(source: bytes, function: str) -> bytes | None:
    """Return `source` with the two vacuity probes added to every definition of `function`.

    Args:
        source (bytes): The C source.
        function (str): The function to probe.

    Returns:
        bytes | None: The probed source, or None when `function` is not defined in it.
    """
    offsets = function_body_offsets(source, function)
    if not offsets:
        return None
    probed = source
    for offset in sorted(offsets, reverse=True):
        probed = (
            probed[:offset]
            + b'__CPROVER_ensures(0)\n{ __CPROVER_assert(0, "'
            + _ENTRY_PROBE.encode("ascii")
            + b'");'
            + probed[offset + 1 :]
        )
    return probed


def add_entry_probe(source: bytes, function: str) -> bytes | None:
    """Return `source` with every body of `function` starting with the entry probe and a dead end.

    The false assertion fails exactly when an execution enters the body, and the
    `__CPROVER_assume(0)` after it ends every path there, so CBMC never explores the rest of the
    body and a run without the depth bound stays short.

    Args:
        source (bytes): The C source.
        function (str): The function to probe.

    Returns:
        bytes | None: The probed source, or None when `function` is not defined in it.
    """
    offsets = function_body_offsets(source, function)
    if not offsets:
        return None
    probed = source
    for offset in sorted(offsets, reverse=True):
        probed = (
            probed[:offset]
            + b'{ __CPROVER_assert(0, "'
            + _ENTRY_PROBE.encode("ascii")
            + b'"); __CPROVER_assume(0);'
            + probed[offset + 1 :]
        )
    return probed


def classify_entry_probe_run(result: RunCbmcResult) -> VacuityCheckResult:
    """Return why no execution entered the body, from the run without the depth bound.

    This run is only made once the probed run has found that no execution enters the body
    within the bound.

    Args:
        result (RunCbmcResult): The outcome of running the pipeline, without `--depth`, on a copy
            made by `add_entry_probe`.

    Returns:
        VacuityCheckResult: `DEPTH_LIMIT_REACHED` when the entry probe fails, so that an
            execution enters the body once the bound is lifted; `UNSATISFIABLE_PRECONDITIONS`
            otherwise, with a message when the run gave no answer.
    """
    if result.failed_step is CbmcStep.CBMC and not result.timed_out:
        failures = [
            line for line in result.response.splitlines() if line.rstrip().endswith("FAILURE")
        ]
        if any(_ENTRY_PROBE in line for line in failures):
            return VacuityCheckResult(
                Vacuity.DEPTH_LIMIT_REACHED,
                "an execution enters the body when CBMC runs without its depth bound",
            )
    if result.cbmc_ran_successfully and result.returncode == 0:
        return VacuityCheckResult(Vacuity.UNSATISFIABLE_PRECONDITIONS, "")
    return VacuityCheckResult(
        Vacuity.UNSATISFIABLE_PRECONDITIONS,
        f"the check without the depth bound gave no answer ({result!s})",
    )


def classify_probe_run(result: RunCbmcResult) -> VacuityCheckResult:
    """Return what a run of the pipeline on a probed copy says about vacuity.

    The function verified without the probes, so every property the probed run reports as failed
    is one of the two probes.

    Args:
        result (RunCbmcResult): The outcome of running the pipeline on the probed copy.

    Returns:
        VacuityCheckResult: The result of checking the function specification for vacuity.
    """
    if result.timed_out:
        return VacuityCheckResult(Vacuity.UNKNOWN, "the vacuity run timed out")
    if result.cbmc_ran_successfully and result.returncode == 0:
        # Not even the assertion at the top of the body was reached. `check_vacuity` then finds
        # out whether the depth bound is why.
        return VacuityCheckResult(Vacuity.UNSATISFIABLE_PRECONDITIONS, "")
    if result.failed_step is not CbmcStep.CBMC:
        return VacuityCheckResult(Vacuity.UNKNOWN, f"the vacuity run ended with {result!s}")
    failures = [line for line in result.response.splitlines() if line.rstrip().endswith("FAILURE")]
    entered = any(_ENTRY_PROBE in line for line in failures)
    left = any(_ENTRY_PROBE not in line for line in failures)
    if entered and left:
        return VacuityCheckResult(Vacuity.NOT_VACUOUS, "")
    if entered:
        return VacuityCheckResult(Vacuity.END_UNREACHABLE, "")
    return VacuityCheckResult(
        Vacuity.UNKNOWN, "the vacuity run failed without reporting either probe"
    )


def describe_vacuous_result(function: str, vacuity_check_result: VacuityCheckResult) -> str:
    """Return the message the agent is shown when `function` verifies vacuously.

    Args:
        function (str): The function.
        vacuity_check_result (VacuityCheckResult): The vacuity in the result must be one of
            `VACUOUS_KINDS`.

    Returns:
        str: What was found, why it means the proof is worthless, and what usually causes it.
    """
    vacuity_type = vacuity_check_result.vacuity
    if vacuity_type not in VACUOUS_KINDS:
        msg = f"Attempted to describe a non-vacuous result: {vacuity_check_result}"
        raise Exception(msg)
    header = (
        f"{function} verified VACUOUSLY, which does not count as verified: CBMC reported no "
        "failure, but the same run still reports none when a false postcondition "
        "(__CPROVER_ensures(0)) is added to the contract. No execution reaches the end of the "
        "function, so none of your postconditions was actually checked, and every mutant would "
        "survive. Mutation testing was skipped.\n\n"
    )
    if vacuity_type is Vacuity.UNSATISFIABLE_PRECONDITIONS:
        return header + (
            "Cause: no execution even enters the body (a false assertion placed as its first "
            "statement is never reached). The __CPROVER_requires clauses cannot all hold at "
            "once.\n"
            "Usual reasons:\n"
            "- Two clauses contradict each other (check bounds that exclude one another).\n"
            "- A void * parameter is required to be readable or writable for more than one "
            "byte with __CPROVER_r_ok / __CPROVER_w_ok alone. Write __CPROVER_r_ok(p, n) || "
            "__CPROVER_is_fresh(p, n) (or the w_ok form) instead.\n"
            "- A clause relates two pointer parameters to each other: __CPROVER_same_object(p, "
            "q), a comparison of their __CPROVER_POINTER_OFFSETs, p < q, or q == p + k. Two "
            "parameters never share an object when the function is verified, so no wording of "
            "such a relation can hold; CLAUDE.md says what to do then.\n"
            "- A clause dereferences a pointer before the clause that makes it valid.\n"
            "To find the clause, remove the __CPROVER_requires clauses one at a time and re-run: "
            "the result stops being VACUOUS when the offending clause is gone. Then restate it."
        )
    return header + (
        "Cause: executions enter the body (a false assertion placed as its first statement is "
        "reached) but none reaches the end of the function.\n"
        "Usual reasons:\n"
        "- The contract of a function this one calls promises something impossible, so nothing "
        "continues past the call. Check the __CPROVER_ensures clauses of its in-file callees; "
        "you may fix a callee's contract.\n"
        "- The preconditions allow only inputs whose executions are longer than the harness's "
        "loop and depth bounds, so every path is cut off. Bound sizes and counts by smaller "
        "constants so that a complete execution fits.\n"
        "- The function never returns for the inputs the preconditions allow."
    )
