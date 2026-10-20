"""Decide whether a function that verifies does so vacuously.

A contract is checked by assuming its preconditions, running the body, and asserting its
postconditions wherever the function returns. If no execution CBMC explores gets that far, there
is nothing to assert and the run passes whatever the postconditions say. That happens when the
preconditions contradict one another, when a callee's contract promises something impossible, or
when the harness's loop and depth bounds cut every path short. Such a pass proves nothing, and
every mutant of the function survives it.

The check is one extra run of the pipeline on a copy of the file with two probes added to the
function: a postcondition that is false (`__CPROVER_ensures(0)`) and an assertion that is false as
the first statement of the body. A false assertion *fails* exactly when an execution reaches it,
so:

- neither probe fails: no execution enters the body; the preconditions cannot all hold;
- only the entry probe fails: executions enter the body but none reaches the end;
- both fail: the function is entered and left, so its postconditions were really checked.

The copy lives in a temporary directory and is never seen by a session or by a merge.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from tools.run_cbmc import CbmcStep, PipelineVariant, RunCbmcResult, run_cbmc
from tools.util.edit_rules import function_body_offsets
from tools.util.vacuity_check_result import Vacuity, VacuityCheckResult

# The message of the assertion added as the first statement of the body. CBMC prints it on the
# line that reports the assertion, which is how the two probes are told apart.
_ENTRY_PROBE = "AVOCADO_VACUITY_PROBE_BODY_ENTERED"

_TEMP_DIR_PREFIX = "avocado-vacuity-"


def check_vacuity(
    function: str,
    file_path: str,
    include_dirs: list[str] | None = None,
    *,
    variant: PipelineVariant | None = None,
) -> VacuityCheckResult:
    """Return whether `function`, which verifies in `file_path`, does so vacuously.

    The probed copy is run through the same variant of the pipeline as the run that verified,
    and only that one. The probed run fails by design; left to itself, `run_cbmc` could take
    that failure as a reason to retry differently, and the verdict would then be about a
    pipeline other than the one whose pass is being checked.

    Args:
        function (str): A function that verifies.
        file_path (str): Path to the C file defining it.
        include_dirs (list[str] | None): Directories forwarded to `goto-cc` as `-I` flags.
        variant (PipelineVariant | None): The variant of the pipeline in which the function
            verified (`RunCbmcResult.variant`); None means the plain pipeline.

    Returns:
        VacuityCheckResult: The result of checking the function specification for vacuity.
    """
    source_path = Path(file_path).resolve()
    try:
        probed = add_probes(source_path.read_bytes(), function)
    except OSError as error:
        return VacuityCheckResult(Vacuity.UNKNOWN, f"could not read {source_path}: {error}")
    if probed is None:
        return VacuityCheckResult(
            Vacuity.UNKNOWN, f"no definition of {function} was found to probe"
        )
    with tempfile.TemporaryDirectory(prefix=_TEMP_DIR_PREFIX) as scratch:
        probe_dir = Path(scratch) / "source"
        work_dir = Path(scratch) / "work"
        probe_dir.mkdir()
        work_dir.mkdir()
        probe_file = probe_dir / source_path.name
        probe_file.write_bytes(probed)
        result = run_cbmc(
            function_to_verify=function,
            file_containing_function_to_verify=str(probe_file),
            # The copy is alone in its directory, so the headers beside the original are found
            # through the include path instead.
            include_dirs=[
                str(source_path.parent),
                *(str(Path(directory).resolve()) for directory in include_dirs or []),
            ],
            cwd=str(work_dir),
            variant=variant or PipelineVariant(),
        )
    return classify_probe_run(result)


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
        # Not even the assertion at the top of the body was reached.
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
            `UNSATISFIABLE_PRECONDITIONS` or `END_UNREACHABLE`.

    Returns:
        str: What was found, why it means the proof is worthless, and what usually causes it.
    """
    vacuity_type = vacuity_check_result.vacuity
    if vacuity_type not in {Vacuity.UNSATISFIABLE_PRECONDITIONS, Vacuity.END_UNREACHABLE}:
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
