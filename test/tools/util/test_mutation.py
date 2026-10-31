"""Tests for the concurrency-related pieces of `tools.util.mutation`."""

from tools.run_cbmc import max_concurrent_subprocesses
from tools.util.mutation import _mutation_worker_count


def test_mutation_worker_count_is_bounded_by_subprocess_slots_and_mutants() -> None:
    slots = max_concurrent_subprocesses()
    assert _mutation_worker_count(0) == 1
    assert _mutation_worker_count(1) == 1
    assert _mutation_worker_count(10_000) == slots
    assert _mutation_worker_count(min(3, slots)) == min(3, slots)
