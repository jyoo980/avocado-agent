"""Tests for functions that pass CBMC only because they have no contract to check."""

from pathlib import Path

import pytest

import tools.run_cbmc
from avocado_verify import ClaudeRun, GroundTruthVerificationResult, _outcome_for
from eval.verify_program import _verify_function
from tools.run_cbmc import RunCbmcResult, has_contract, verify_function

SOURCE = b"""int answer(void) { return 42; }

int clear(int *p)
__CPROVER_requires(__CPROVER_is_fresh(p, sizeof(int)))
__CPROVER_assigns(*p)
{ *p = 0; return 0; }

#ifdef AVOCADO_TEST_NEVER_DEFINED
int pick(int x)
__CPROVER_ensures(__CPROVER_return_value == x)
{ return x; }
#else
int pick(int x) { return x; }
#endif
"""


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "contracts.c"
    source.write_bytes(SOURCE)
    return source


def test_the_compiled_program_says_which_functions_have_a_contract(tmp_path: Path) -> None:
    source = str(_source(tmp_path))
    assert has_contract("answer", source) is False
    # Any clause makes a contract, an assigns clause included.
    assert has_contract("clear", source) is True
    # The definition that is compiled decides, not one in a branch the build leaves out.
    assert has_contract("pick", source) is False


def test_a_file_that_does_not_compile_tells_nothing(tmp_path: Path) -> None:
    broken = tmp_path / "broken.c"
    broken.write_text("int f(void) { return }\n")
    assert has_contract("f", str(broken)) is None
    assert has_contract("f", str(tmp_path / "missing.c")) is None


def test_a_function_without_a_contract_is_not_verified(tmp_path: Path) -> None:
    source = str(_source(tmp_path))
    result = verify_function("answer", source)
    assert str(result) == "NO_CONTRACT" and not result.is_function_verified
    assert (
        result.no_contract and result.cbmc_ran_successfully and result.vacuity_check_result is None
    )
    assert "has no contract" in result.response
    assert str(verify_function("pick", source)) == "NO_CONTRACT"
    assert verify_function("clear", source).is_function_verified


def test_no_contract_outranks_the_other_verdicts_of_a_pass() -> None:
    result = RunCbmcResult("f", None, False, 0, "ok", no_contract=True)
    assert str(result) == "NO_CONTRACT" and not result.is_function_verified


def test_the_ground_truth_verdict_of_a_function_without_a_contract_is_not_verified() -> None:
    claude = ClaudeRun(0, False, False, None, "done", None, None, None, "success")
    no_contract = RunCbmcResult("f", None, False, 0, "ok", no_contract=True)
    assert _outcome_for(claude, no_contract) == GroundTruthVerificationResult.UNVERIFIED
    verified = RunCbmcResult("f", None, False, 0, "ok")
    assert _outcome_for(claude, verified) == GroundTruthVerificationResult.VERIFIED


def test_the_evaluation_does_not_count_a_function_without_a_contract(tmp_path: Path) -> None:
    source = str(_source(tmp_path))
    assert str(_verify_function("answer", source).run_cbmc_result) == "NO_CONTRACT"
    assert not _verify_function("answer", source).is_function_verified
    assert _verify_function("clear", source).is_function_verified


def _verify_with_probe(monkeypatch: pytest.MonkeyPatch, probe: bool | None) -> RunCbmcResult:
    """Run `verify_function` on a passing pipeline whose contract probe returns `probe`.

    Args:
        monkeypatch (pytest.MonkeyPatch): Used to stub out the pipeline and the probe.
        probe (bool | None): What the stubbed `has_contract` returns.

    Returns:
        RunCbmcResult: The result `verify_function` returns.
    """
    passing = RunCbmcResult("f", None, False, 0, "ok")
    monkeypatch.setattr(tools.run_cbmc, "run_cbmc", lambda **_: passing)
    monkeypatch.setattr(tools.run_cbmc, "has_contract", lambda *_: probe)
    return verify_function("f", "f.c")


def test_a_failed_contract_probe_is_a_distinct_unverified_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _verify_with_probe(monkeypatch, None)
    assert str(result) == "CONTRACT_PROBE_FAILED" and not result.is_function_verified
    assert result.contract_probe_failed and not result.no_contract
    assert result.cbmc_ran_successfully
    assert "could not be determined" in result.response
    assert "has no contract" not in result.response


def test_only_a_probe_that_finds_no_contract_sets_no_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = _verify_with_probe(monkeypatch, False)
    assert str(missing) == "NO_CONTRACT" and missing.no_contract
    assert not missing.contract_probe_failed and "has no contract" in missing.response
    present = _verify_with_probe(monkeypatch, True)
    assert str(present) == "PASS" and present.is_function_verified
    assert not present.no_contract and not present.contract_probe_failed


def test_the_ground_truth_verdict_of_a_failed_contract_probe_is_not_verified() -> None:
    claude = ClaudeRun(0, False, False, None, "done", None, None, None, "success")
    probe_failed = RunCbmcResult("f", None, False, 0, "ok", contract_probe_failed=True)
    assert _outcome_for(claude, probe_failed) == GroundTruthVerificationResult.UNVERIFIED
