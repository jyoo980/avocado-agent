"""Tests for the concurrency-related pieces of `tools.util.mutation`."""

import os
from pathlib import Path

from tools.util.mutation import _get_path_for_mutated_source, _get_paths_for_mutated_sources


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


def test_mutant_path_is_bounded_for_long_names() -> None:
    workspace = Path("/work")
    source = Path("/src/foo.c")
    long_a = "f" * 240 + "a"
    long_b = "f" * 240 + "b"
    path_a = _get_path_for_mutated_source(workspace, source, long_a, 3)
    path_b = _get_path_for_mutated_source(workspace, source, long_b, 3)
    for path in (path_a, path_b):
        assert len(os.fsencode(path.name)) <= 255
        assert "__mutant_" in path.name
        assert path.suffix == ".c"
    assert path_a != path_b
    assert path_a != _get_path_for_mutated_source(workspace, source, long_a, 4)


def test_mutant_paths_skip_the_source_files_symlink_target(tmp_path: Path) -> None:
    # `foo.c` is a symlink to a file whose name matches the first mutant path. Writing a mutant
    # there would overwrite the source, and cleanup would delete it.
    target = tmp_path / "foo__mutant_bar_0.c"
    target.write_text("int bar(void) { return 0; }\n", encoding="utf-8")
    source = tmp_path / "foo.c"
    source.symlink_to(target.name)
    paths = _get_paths_for_mutated_sources(tmp_path, source, "bar", 2)
    assert paths == [tmp_path / "foo__mutant_bar_1.c", tmp_path / "foo__mutant_bar_2.c"]


def test_mutant_paths_use_consecutive_indices_without_a_collision(tmp_path: Path) -> None:
    source = tmp_path / "foo.c"
    source.write_text("int bar(void) { return 0; }\n", encoding="utf-8")
    assert _get_paths_for_mutated_sources(tmp_path, source, "bar", 2) == [
        _get_path_for_mutated_source(tmp_path, source, "bar", 0),
        _get_path_for_mutated_source(tmp_path, source, "bar", 1),
    ]
