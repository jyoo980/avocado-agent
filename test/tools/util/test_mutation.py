"""Tests for the concurrency-related pieces of `tools.util.mutation`."""

import os
from pathlib import Path

from tools.util.mutation import _get_path_for_mutated_source


def test_mutant_path_includes_function_name_so_functions_never_collide() -> None:
    source = Path("/src/foo.c")
    assert _get_path_for_mutated_source(source, "bar", 1) == Path("/src/foo__mutant_bar_1.c")
    # Two functions of one file get distinct paths for the same mutant index.
    assert _get_path_for_mutated_source(source, "bar", 0) != (
        _get_path_for_mutated_source(source, "baz", 0)
    )
    # The name still matches the `*__mutant_*.c` pattern that `make clean-mutants` removes.
    assert "__mutant_" in _get_path_for_mutated_source(source, "bar", 0).name


def test_mutant_path_is_bounded_for_long_names() -> None:
    source = Path("/src/foo.c")
    long_a = "f" * 240 + "a"
    long_b = "f" * 240 + "b"
    path_a = _get_path_for_mutated_source(source, long_a, 3)
    path_b = _get_path_for_mutated_source(source, long_b, 3)
    for path in (path_a, path_b):
        assert len(os.fsencode(path.name)) <= 255
        assert "__mutant_" in path.name
        assert path.suffix == ".c"
    assert path_a != path_b
    assert path_a != _get_path_for_mutated_source(source, long_a, 4)
