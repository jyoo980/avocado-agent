"""Tests for the concurrency-related pieces of `tools.util.mutation`."""

from pathlib import Path

from tools.run_cbmc import max_concurrent_subprocesses
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


def test_mutation_worker_count_is_bounded_by_subprocess_slots_and_mutants() -> None:
    slots = max_concurrent_subprocesses()
    assert _mutation_worker_count(0) == 1
    assert _mutation_worker_count(1) == 1
    assert _mutation_worker_count(10_000) == slots
    assert _mutation_worker_count(min(3, slots)) == min(3, slots)
