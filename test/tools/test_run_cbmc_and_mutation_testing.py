"""Tests for the `avocado-run-cbmc` entry point in `tools.run_cbmc_and_mutation_testing`."""

from tools.run_cbmc import _DEFAULT_RUN_CBMC_TIMEOUT_SEC
from tools.run_cbmc_and_mutation_testing import _AGENT_MUTANT_TIMEOUT_SEC
from tools.util.mutation import DEFAULT_MUTANT_TIMEOUT_SEC


def test_agent_tool_uses_a_shorter_mutant_budget_than_the_metric() -> None:
    # The evaluation metric keeps the full pipeline timeout; only the agent-facing tool trims it.
    assert DEFAULT_MUTANT_TIMEOUT_SEC == _DEFAULT_RUN_CBMC_TIMEOUT_SEC
    assert 0 < _AGENT_MUTANT_TIMEOUT_SEC < DEFAULT_MUTANT_TIMEOUT_SEC
