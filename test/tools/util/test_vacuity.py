"""Tests for `tools.util.vacuity`: telling a proof that proves nothing from a real one."""

from pathlib import Path

import tools.run_cbmc
from tools.run_cbmc import DEFAULT_CBMC_DEPTH, CbmcStep, PipelineVariant, RunCbmcResult, run_cbmc
from tools.run_cbmc_and_mutation_testing import verify_function
from tools.util import vacuity_check_result
from tools.util.vacuity import (
    add_entry_probe,
    add_probes,
    check_vacuity,
    classify_entry_probe_run,
    classify_probe_run,
    describe_vacuous_result,
)
from tools.util.vacuity_check_result import Vacuity, VacuityCheckResult

SOURCE = b"""int clamp(int v)
__CPROVER_requires(v >= 0)
__CPROVER_ensures(__CPROVER_return_value <= 8)
{
    if (v > 8) { return 8; }
    return v;
}

/* The preconditions contradict each other, so the body is never entered. */
int contradictory(int v)
__CPROVER_requires(v > 0)
__CPROVER_requires(v < 0)
__CPROVER_ensures(__CPROVER_return_value == 12345)
{
    return v;
}

/* The body is entered, but no execution gets past the assumption. */
int never_returns(int v)
__CPROVER_requires(v >= 0)
__CPROVER_ensures(__CPROVER_return_value == 12345)
{
    __CPROVER_assume(v < 0);
    return v;
}
"""


def _probe_run(*, returncode: int, failed_step: CbmcStep | None, response: str) -> RunCbmcResult:
    return RunCbmcResult(
        function="f",
        failed_step=failed_step,
        timed_out=False,
        returncode=returncode,
        response=response,
    )


def test_probes_go_into_the_contract_and_the_top_of_the_body_of_every_definition() -> None:
    source = (
        b"#ifdef FAST\nstatic int pick(int x) { return x; }\n#else\n"
        b"static int pick(int x)\n__CPROVER_ensures(__CPROVER_return_value == x)\n"
        b"{ return x; }\n#endif\nint other(int x) { return x; }\n"
    )
    probed = add_probes(source, "pick")
    assert probed is not None
    assert probed.count(b"__CPROVER_ensures(0)") == 2 and probed.count(b"__CPROVER_assert(0") == 2
    # The false postcondition closes the contract; the assertion opens the body.
    assert b'__CPROVER_ensures(0)\n{ __CPROVER_assert(0, "AVOCADO_VACUITY_PROBE_BODY_ENTERED");' in probed
    assert b"int other(int x) { return x; }" in probed
    assert add_probes(source, "missing") is None


def test_a_probe_run_is_read_by_which_of_the_two_probes_failed() -> None:
    entry = "[f.assertion.1] line 4 AVOCADO_VACUITY_PROBE_BODY_ENTERED: FAILURE"
    end = "[f.postcondition.2] line 3 Check ensures clause: FAILURE"
    cbmc = {"returncode": 10, "failed_step": CbmcStep.CBMC}
    vacuity_check_result = classify_probe_run(_probe_run(response=f"{end}\n{entry}\n", **cbmc))
    assert vacuity_check_result.vacuity == Vacuity.NOT_VACUOUS

    vacuity_check_result = classify_probe_run(_probe_run(response=f"{entry}\n", **cbmc))
    assert vacuity_check_result.vacuity == Vacuity.END_UNREACHABLE
    
    # Nothing failed: not even the assertion at the top of the body was reached.
    vacuity_check_result = classify_probe_run(_probe_run(returncode=0, failed_step=None, response="f verified successfully"))
    assert vacuity_check_result.vacuity == Vacuity.UNSATISFIABLE_PRECONDITIONS


def test_a_probe_run_that_gives_no_answer_is_unknown() -> None:
    # Timeouts.
    cbmc_timed_out = RunCbmcResult(
        function="f", failed_step=CbmcStep.CBMC, timed_out=True, returncode=124, response=""
    )
    vacuity_result = classify_probe_run(cbmc_timed_out)
    assert vacuity_result.vacuity == Vacuity.UNKNOWN
    
    # Syntax errors.
    broken = _probe_run(returncode=1, failed_step=CbmcStep.GOTO_CC, response="syntax error")
    vacuity_result = classify_probe_run(broken)
    assert vacuity_result.vacuity is Vacuity.UNKNOWN and "GOTO-CC_FAILED" in vacuity_result.message

    failed_run = _probe_run(returncode=10, failed_step=CbmcStep.CBMC, response="no property lines")
    assert classify_probe_run(failed_run).vacuity == Vacuity.UNKNOWN


def test_a_vacuous_result_does_not_count_as_verified() -> None:
    passed = RunCbmcResult(
        function="f", failed_step=None, timed_out=False, returncode=0, response="ok"
    )
    assert passed.is_function_verified and str(passed) == "PASS"
    # A pass whose body CBMC could not enter within the depth bound stands, like an unknown one.
    for vacuity in (Vacuity.NOT_VACUOUS, Vacuity.UNKNOWN, Vacuity.DEPTH_LIMIT_REACHED):
        checked = RunCbmcResult("f", None, False, 0, "ok", vacuity_check_result=VacuityCheckResult(vacuity, ""))
        assert checked.is_function_verified and not checked.is_vacuous and str(checked) == "PASS"

    for vacuity in (Vacuity.UNSATISFIABLE_PRECONDITIONS, Vacuity.END_UNREACHABLE):
        vacuous = RunCbmcResult("f", None, False, 0, "ok", vacuity_check_result=VacuityCheckResult(vacuity, ""))
        assert not vacuous.is_function_verified and vacuous.is_vacuous
        assert str(vacuous) == "VACUOUS" and vacuous.cbmc_ran_successfully
        assert "does not count as verified" in describe_vacuous_result("f", VacuityCheckResult(vacuity, ""))


def test_cbmc_tells_the_three_cases_apart(tmp_path: Path) -> None:
    source = tmp_path / "vacuity.c"
    source.write_bytes(SOURCE)
    assert not check_vacuity("clamp", str(source), depth=DEFAULT_CBMC_DEPTH).is_vacuous 
    assert check_vacuity("contradictory", str(source), depth=DEFAULT_CBMC_DEPTH).vacuity is Vacuity.UNSATISFIABLE_PRECONDITIONS
    assert check_vacuity("never_returns", str(source), depth=DEFAULT_CBMC_DEPTH).vacuity is Vacuity.END_UNREACHABLE
    # The probes go into a copy elsewhere: the file and its directory are as they were.
    assert source.read_bytes() == SOURCE
    assert [path.name for path in tmp_path.iterdir()] == ["vacuity.c"]


def test_verify_function_reports_a_vacuous_pass_as_vacuous(tmp_path: Path) -> None:
    source = tmp_path / "vacuity.c"
    source.write_bytes(SOURCE)
    vacuous_cbmc_run = verify_function("contradictory", str(source), depth=DEFAULT_CBMC_DEPTH)
    assert not vacuous_cbmc_run.is_function_verified and str(vacuous_cbmc_run) == "VACUOUS"

    assert vacuous_cbmc_run.vacuity_check_result is not None, f"{vacuous_cbmc_run} has a 'None' vacuity_check_result field"
    assert vacuous_cbmc_run.vacuity_check_result.vacuity is Vacuity.UNSATISFIABLE_PRECONDITIONS
    assert "no execution even enters the body" in vacuous_cbmc_run.response

    non_terminating_cbmc_run = verify_function("never_returns", str(source), depth=DEFAULT_CBMC_DEPTH)
    assert non_terminating_cbmc_run.vacuity_check_result is not None, f"{non_terminating_cbmc_run} has a 'None' vacuity_check_result field"

    assert str(non_terminating_cbmc_run) == "VACUOUS" and non_terminating_cbmc_run.vacuity_check_result.vacuity is Vacuity.END_UNREACHABLE
    assert "none reaches the end of the function" in non_terminating_cbmc_run.response

    # Without the check, the false postcondition of `contradictory` passes unnoticed.
    unchecked_cbmc_run = verify_function("contradictory", str(source), depth=DEFAULT_CBMC_DEPTH, vacuity_check=False)
    assert unchecked_cbmc_run.is_function_verified and unchecked_cbmc_run.vacuity_check_result is None


def test_the_probe_run_uses_the_variant_that_passed_and_is_never_retried(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "vacuity.c"
    source.write_bytes(SOURCE)
    runs: list[bool] = []

    def failing_pipeline(function, callees, file, **kwargs):
        del callees, file
        runs.append(kwargs["prevent_macro_expansion"])
        failure = RunCbmcResult(
            function=function,
            failed_step=CbmcStep.CBMC,
            timed_out=False,
            returncode=10,
            response="[clamp.assertion.1] line 4 AVOCADO_VACUITY_PROBE_BODY_ENTERED: FAILURE",
        )
        # The output a failing run needs for `run_cbmc` to retry with macros disabled.
        return failure, "", "no body for function 'memcpy'"

    monkeypatch.setattr(tools.run_cbmc, "_run_pipeline", failing_pipeline)
    monkeypatch.setattr(tools.run_cbmc, "_log_invocation", lambda *a, **k: None)
    # An ordinary run takes that failure as a reason to run the other variant.
    retried = run_cbmc("clamp", str(source))
    assert runs == [False, True] and retried.variant == PipelineVariant(
        prevent_macro_expansion=True
    )
    # The probe run is made once, in the variant it is told, so its verdict is about that one.
    runs.clear()
    probe = run_cbmc("clamp", str(source), variant=PipelineVariant())
    assert runs == [False] and probe.variant == PipelineVariant()
    runs.clear()
    cbmc_run_for_clamp = check_vacuity("clamp", str(source), variant=PipelineVariant(True, True), depth=DEFAULT_CBMC_DEPTH)
    assert cbmc_run_for_clamp.vacuity == Vacuity.END_UNREACHABLE
    assert runs == [True]


def _deep_source() -> bytes:
    """A function whose precondition asks for more steps than the depth bound before the body."""
    steps = "".join(f"    x = x + {index % 3};\n" for index in range(300))
    return (
        "static int avocado_spec_long(int x)\n{\n" + steps + "    return x;\n}\n\n"
        "int deep(int v)\n"
        "__CPROVER_requires(v >= 0 && v < 4 && avocado_spec_long(v) >= 0)\n"
        "__CPROVER_ensures(__CPROVER_return_value == v)\n"
        "{\n    return v;\n}\n"
    ).encode("ascii")


def test_the_entry_probe_ends_every_path_at_the_top_of_every_definition() -> None:
    source = (
        b"#ifdef FAST\nstatic int pick(int x) { return x; }\n#else\n"
        b"static int pick(int x)\n__CPROVER_ensures(__CPROVER_return_value == x)\n"
        b"{ return x; }\n#endif\nint other(int x) { return x; }\n"
    )
    probed = add_entry_probe(source, "pick")
    assert probed is not None
    probe = b'{ __CPROVER_assert(0, "AVOCADO_VACUITY_PROBE_BODY_ENTERED"); __CPROVER_assume(0);'
    assert probed.count(probe) == 2
    # Unlike the other probes, it adds no false postcondition.
    assert b"__CPROVER_ensures(0)" not in probed
    assert b"int other(int x) { return x; }" in probed
    assert add_entry_probe(source, "missing") is None


def test_the_run_without_the_depth_bound_tells_the_depth_limit_from_a_contradiction() -> None:
    entry = "[f.assertion.1] line 4 AVOCADO_VACUITY_PROBE_BODY_ENTERED: FAILURE"
    entered = _probe_run(returncode=10, failed_step=CbmcStep.CBMC, response=f"{entry}\n")
    assert classify_entry_probe_run(entered).vacuity is Vacuity.DEPTH_LIMIT_REACHED
    # Not reached even without the bound: the preconditions cannot all hold.
    not_entered = _probe_run(returncode=0, failed_step=None, response="f verified successfully")
    result = classify_entry_probe_run(not_entered)
    assert result.vacuity is Vacuity.UNSATISFIABLE_PRECONDITIONS and result.message == ""
    # A run that gives no answer leaves the first verdict standing, and says so.
    timed_out = RunCbmcResult(
        function="f", failed_step=CbmcStep.CBMC, timed_out=True, returncode=124, response=""
    )
    result = classify_entry_probe_run(timed_out)
    assert result.vacuity is Vacuity.UNSATISFIABLE_PRECONDITIONS and "no answer" in result.message


def test_a_precondition_that_uses_up_the_depth_bound_is_told_apart(tmp_path: Path) -> None:
    source = tmp_path / "deep.c"
    source.write_bytes(_deep_source())
    result = check_vacuity("deep", str(source), depth=DEFAULT_CBMC_DEPTH)
    assert result.vacuity is Vacuity.DEPTH_LIMIT_REACHED and not result.is_vacuous
    # Like an unknown result, the pass stands, and the kind is kept on the result.
    verified = verify_function("deep", str(source), depth=DEFAULT_CBMC_DEPTH)
    assert verified.is_function_verified and not verified.is_vacuous and str(verified) == "PASS"
    assert verified.vacuity_check_result is not None
    assert verified.vacuity_check_result.vacuity is Vacuity.DEPTH_LIMIT_REACHED
    # A real contradiction is still reported as one.
    contradictory = tmp_path / "vacuity.c"
    contradictory.write_bytes(SOURCE)
    result = check_vacuity("contradictory", str(contradictory), depth=DEFAULT_CBMC_DEPTH)
    assert result.vacuity is Vacuity.UNSATISFIABLE_PRECONDITIONS
    assert source.read_bytes() == _deep_source()
