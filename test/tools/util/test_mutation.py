"""Tests for `tools.util.mutation`."""

import os
from pathlib import Path

import pytest

from tools.util.mutation import (
    MutationScore,
    _get_path_for_mutated_source,
    generate_mutants_and_compute_score,
)


def test_mutation_testing_leaves_no_per_mutant_files(isolated_inc_source: Path) -> None:
    # The `isolated_inc_source` fixture checks for leftover files.
    result = generate_mutants_and_compute_score(str(isolated_inc_source), "inc")
    assert isinstance(result, MutationScore) and result.num_mutants > 0


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


def test_mutant_path_of_a_symlink_is_never_its_target(tmp_path: Path) -> None:
    # If the mutant were named for `foo.c` alone, its path would be the target, and writing the
    # mutant would overwrite the source.
    target = tmp_path / "foo__mutant_bar_0.c"
    target.write_text("int bar(void) { return 0; }\n", encoding="utf-8")
    source = tmp_path / "foo.c"
    try:
        source.symlink_to(target.name)
    except OSError as e:
        pytest.skip(f"cannot create symlinks: {e}")
    assert _get_path_for_mutated_source(tmp_path, source, "bar", 0) == (
        tmp_path / "foo__foo__mutant_bar_0__mutant_bar_0.c"
    )


def test_mutant_paths_of_a_symlink_and_a_sibling_named_like_its_target_differ(
    tmp_path: Path,
) -> None:
    # `a.c -> x/foo.c` and a regular sibling `foo.c` may be scored concurrently; their mutants
    # must not overwrite one another.
    (tmp_path / "x").mkdir()
    target = tmp_path / "x" / "foo.c"
    target.write_text("int bar(void) { return 0; }\n", encoding="utf-8")
    sibling = tmp_path / "foo.c"
    sibling.write_text("int bar(void) { return 1; }\n", encoding="utf-8")
    source = tmp_path / "a.c"
    try:
        source.symlink_to(Path("x") / "foo.c")
    except OSError as e:
        pytest.skip(f"cannot create symlinks: {e}")
    assert _get_path_for_mutated_source(tmp_path, source, "bar", 0) == (
        tmp_path / "a__foo__mutant_bar_0.c"
    )
    assert _get_path_for_mutated_source(tmp_path, sibling, "bar", 0) == (
        tmp_path / "foo__mutant_bar_0.c"
    )
