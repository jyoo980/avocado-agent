"""Tests for the concurrency-related pieces of `tools.util.mutation`."""

import os
import threading
import time
from pathlib import Path

from tools import run_cbmc as run_cbmc_module
from tools.run_cbmc import CbmcStep, _run_command
from tools.util.mutation import _get_path_for_mutated_source, _mutation_worker_count


def test_mutant_path_includes_function_name_so_functions_never_collide() -> None:
    workspace = Path("/work")
    source = Path("/src/foo.c")
    assert _get_path_for_mutated_source(workspace, source, "bar", 1) == Path(
        "/work/foo__mutant_bar_1.c"
    )
    # Two functions of one file get distinct paths for the same mutant index.
    assert _get_path_for_mutated_source(workspace, source, "bar", 0) != (
        _get_path_for_mutated_source(workspace, source, "baz", 0)
    )
    # The name still matches the `*__mutant_*.c` pattern that `make clean-mutants` removes.
    assert "__mutant_" in _get_path_for_mutated_source(workspace, source, "bar", 0).name


def test_mutation_worker_count_is_bounded_only_by_cpus_and_mutants() -> None:
    cpus = os.cpu_count() or 1
    assert _mutation_worker_count(0) == 1
    assert _mutation_worker_count(1) == 1
    assert _mutation_worker_count(10_000) == cpus
    assert _mutation_worker_count(min(3, cpus)) == min(3, cpus)


def test_run_command_is_gated_by_the_subprocess_semaphore(monkeypatch) -> None:
    # With a single slot, two concurrent 0.3 s commands must run back to back (>= 0.6 s total);
    # without the gate they would overlap and finish in ~0.3 s.
    monkeypatch.setattr(run_cbmc_module, "_SUBPROCESS_SLOTS", threading.BoundedSemaphore(1))
    results: list = []

    def run() -> None:
        results.append(_run_command(CbmcStep.CBMC, "sleep 0.3", []))

    threads = [threading.Thread(target=run) for _ in range(2)]
    start = time.monotonic()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    elapsed = time.monotonic() - start
    assert elapsed >= 0.6, f"commands overlapped despite a one-slot semaphore ({elapsed:.2f}s)"
    assert all(result.succeeded for result in results)


def test_run_command_honours_a_custom_timeout() -> None:
    result = _run_command(CbmcStep.CBMC, "sleep 5", [], timeout_sec=1)
    assert result.timed_out
    assert result.timeout_sec == 1
    assert not result.succeeded


def test_agent_tool_uses_a_shorter_mutant_budget_than_the_metric() -> None:
    from tools.run_cbmc import _DEFAULT_RUN_CBMC_TIMEOUT_SEC
    from tools.run_cbmc_and_mutation_testing import _AGENT_MUTANT_TIMEOUT_SEC
    from tools.util.mutation import DEFAULT_MUTANT_TIMEOUT_SEC

    # The evaluation metric keeps the full pipeline timeout; only the agent-facing tool trims it.
    assert DEFAULT_MUTANT_TIMEOUT_SEC == _DEFAULT_RUN_CBMC_TIMEOUT_SEC
    assert 0 < _AGENT_MUTANT_TIMEOUT_SEC < DEFAULT_MUTANT_TIMEOUT_SEC
