"""Tests for `tools.util.session_state`: the one place that says what a merge rechecks."""

from tools.run_cbmc import VACUOUS_VERDICT
from tools.util.session_state import (
    is_vacuous_status,
    rechecked_on_change,
    vacuous_status,
)


def test_vacuous_status_carries_the_kind_of_vacuity_when_known() -> None:
    """The status names the kind of vacuity, and falls back to the bare verdict without one."""
    assert vacuous_status("unsatisfiable_preconditions") == "VACUOUS (unsatisfiable_preconditions)"
    assert vacuous_status(None) == VACUOUS_VERDICT
    assert vacuous_status("") == VACUOUS_VERDICT


def test_is_vacuous_status_reads_back_what_vacuous_status_builds() -> None:
    """Every status the builder makes is recognised, and other verdicts are not."""
    assert is_vacuous_status(vacuous_status("end_unreachable"))
    assert is_vacuous_status(vacuous_status(None))
    assert not is_vacuous_status("CBMC_FAILED")
    assert not is_vacuous_status("GOTO_CC_FAILED")
    assert not is_vacuous_status("")


def test_rechecked_on_change_is_the_verified_and_the_vacuous_functions() -> None:
    """A verified function and a vacuous one are rechecked; a failed one is not."""
    verified = frozenset({"quickSort"})
    unverified = {
        "partition": vacuous_status("unsatisfiable_preconditions"),
        "swap": "CBMC_FAILED",
        "main": "GOTO_CC_FAILED",
    }
    assert rechecked_on_change(verified, unverified) == {"quickSort", "partition"}


def test_rechecked_on_change_is_empty_when_nothing_is_verified_or_vacuous() -> None:
    """With no verified function and no vacuous proof there is nothing to recheck."""
    assert rechecked_on_change(set(), {"swap": "CBMC_FAILED"}) == set()
    assert rechecked_on_change(set(), {}) == set()
