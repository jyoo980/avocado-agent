"""Tests for `eval.mutants.compute_clause_redundancy`."""

import os
from pathlib import Path

from eval.mutants.compute_clause_redundancy import _get_path_to_source_with_removed_clause


def test_removed_clause_path_includes_function_name_so_functions_never_collide() -> None:
    workspace = Path("/work")
    source = Path("/src/foo.c")
    assert _get_path_to_source_with_removed_clause(workspace, source, "bar", 1) == Path(
        "/work/foo__clause_drop_bar_1.c"
    )
    # Two functions of one file get distinct paths for the same clause index.
    assert _get_path_to_source_with_removed_clause(workspace, source, "bar", 0) != (
        _get_path_to_source_with_removed_clause(workspace, source, "baz", 0)
    )
    # The name still matches the `*__clause_drop_*.c` pattern that `make clean-mutants` removes.
    assert (
        "__clause_drop_"
        in _get_path_to_source_with_removed_clause(workspace, source, "bar", 0).name
    )


def test_removed_clause_path_is_bounded_for_long_names() -> None:
    workspace = Path("/work")
    source = Path("/src/" + "s" * 240 + ".c")
    long_a = "f" * 200 + "a"
    long_b = "f" * 200 + "b"
    path_a = _get_path_to_source_with_removed_clause(workspace, source, long_a, 3)
    path_b = _get_path_to_source_with_removed_clause(workspace, source, long_b, 3)
    for path in (path_a, path_b):
        assert len(path.name.encode("utf-8")) <= 255
        assert "__clause_drop_" in path.name
        assert path.suffix == ".c"
    assert path_a != path_b
    assert path_a != _get_path_to_source_with_removed_clause(workspace, source, long_a, 4)


def test_removed_clause_path_handles_non_utf8_stems() -> None:
    workspace = Path("/work")
    # A basename containing a byte that is not valid UTF-8 decodes to a lone surrogate.
    short_source = Path(os.fsdecode(b"/src/f\xffoo.c"))
    short_path = _get_path_to_source_with_removed_clause(workspace, short_source, "bar", 0)
    assert os.fsencode(short_path.name) == b"f\xffoo__clause_drop_bar_0.c"

    long_source = Path(os.fsdecode(b"/src/" + b"\xff" * 240 + b".c"))
    long_path = _get_path_to_source_with_removed_clause(workspace, long_source, "f" * 200, 0)
    assert len(os.fsencode(long_path.name)) <= 255
    assert "__clause_drop_" in long_path.name


def test_removed_clause_path_does_not_split_multibyte_characters() -> None:
    workspace = Path("/work")
    source = Path("/src/" + "é" * 120 + ".c")
    path = _get_path_to_source_with_removed_clause(workspace, source, "f" * 200, 0)
    encoded = os.fsencode(path.name)
    assert len(encoded) <= 255
    encoded.decode("utf-8")  # Raises if a multibyte character was split.
