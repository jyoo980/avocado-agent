"""Tests for the `avocado-run-cbmc` entry point in `tools.run_cbmc_and_mutation_testing`."""

from pathlib import Path

import pytest

from tools.run_cbmc import _DEFAULT_RUN_CBMC_TIMEOUT_SEC
from tools.run_cbmc_and_mutation_testing import (
    _AGENT_MUTANT_TIMEOUT_SEC,
    _get_include_dirs_for_run,
)
from tools.util import AUTO_INCLUDE_ENV_VAR
from tools.util.mutation import DEFAULT_MUTANT_TIMEOUT_SEC


def test_agent_tool_uses_a_shorter_mutant_budget_than_the_metric() -> None:
    # The evaluation metric keeps the full pipeline timeout; only the agent-facing tool trims it.
    assert DEFAULT_MUTANT_TIMEOUT_SEC == _DEFAULT_RUN_CBMC_TIMEOUT_SEC
    assert 0 < _AGENT_MUTANT_TIMEOUT_SEC < DEFAULT_MUTANT_TIMEOUT_SEC


def test_include_dirs_for_run_follow_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "include").mkdir()
    (tmp_path / "stubs").mkdir()
    source = str(tmp_path / "src" / "foo.c")
    stubs = str((tmp_path / "stubs").resolve())
    detected = str((tmp_path / "include").resolve())
    monkeypatch.delenv(AUTO_INCLUDE_ENV_VAR, raising=False)
    assert _get_include_dirs_for_run(source, [stubs]) == [stubs, detected]
    monkeypatch.setenv(AUTO_INCLUDE_ENV_VAR, "0")
    assert _get_include_dirs_for_run(source, [stubs]) == [stubs]
