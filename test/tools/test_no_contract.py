"""Tests for functions that pass CBMC only because they have no contract to check."""

from pathlib import Path

from tools.run_cbmc import RunCbmcResult, has_contract
from tools.run_cbmc_and_mutation_testing import verify_function

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
    assert has_contract("answer", str(tmp_path / "missing.c")) is None


def test_a_function_without_a_contract_is_not_verified(tmp_path: Path) -> None:
    source = str(_source(tmp_path))
    result = verify_function("answer", source)
    assert str(result) == "NO_CONTRACT" and not result.is_function_verified
    assert result.no_contract and result.cbmc_ran_successfully and result.vacuity is None
    assert "has no contract" in result.response
    assert str(verify_function("pick", source)) == "NO_CONTRACT"
    assert verify_function("clear", source).is_function_verified


def test_no_contract_outranks_the_other_verdicts_of_a_pass() -> None:
    result = RunCbmcResult("f", None, False, 0, "ok", no_contract=True)
    assert str(result) == "NO_CONTRACT" and not result.is_function_verified
