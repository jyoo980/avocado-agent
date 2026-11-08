"""Tests for `eval.mutants.util`."""

from pathlib import Path

import pytest

from eval.mutants.util import (
    get_existing_run_logs,
    get_mutant_paths,
    remove_mutants_and_run_logs,
)


def _path_for_index(directory: Path, index: int) -> Path:
    return directory / f"foo__mutant_bar_{index}.c"


def test_mutant_paths_skip_the_source_files_symlink_target(tmp_path: Path) -> None:
    # `foo.c` is a symlink to a file whose name matches the first mutant path. Writing a mutant
    # there would overwrite the source, and cleanup would delete it.
    target = tmp_path / "foo__mutant_bar_0.c"
    target.write_text("int bar(void) { return 0; }\n", encoding="utf-8")
    source = tmp_path / "foo.c"
    try:
        source.symlink_to(target.name)
    except OSError as e:
        pytest.skip(f"cannot create symlinks: {e}")
    paths = get_mutant_paths(source, lambda i: _path_for_index(tmp_path, i), 2)
    assert paths == [_path_for_index(tmp_path, 1), _path_for_index(tmp_path, 2)]


def test_mutant_paths_use_consecutive_indices_without_a_collision(tmp_path: Path) -> None:
    source = tmp_path / "foo.c"
    source.write_text("int bar(void) { return 0; }\n", encoding="utf-8")
    # An existing file that is not the source does not cause its index to be skipped.
    _path_for_index(tmp_path, 0).write_text("", encoding="utf-8")
    assert get_mutant_paths(source, lambda i: _path_for_index(tmp_path, i), 2) == [
        _path_for_index(tmp_path, 0),
        _path_for_index(tmp_path, 1),
    ]


def test_remove_mutants_and_run_logs_keeps_preexisting_logs(tmp_path: Path) -> None:
    old = _path_for_index(tmp_path, 0)
    new = _path_for_index(tmp_path, 1)
    old_log = tmp_path / "foo__mutant_bar_0-cbmc-runs.jsonl"
    new_log = tmp_path / "foo__mutant_bar_1-cbmc-runs.jsonl"
    old_log.write_text("{}\n", encoding="utf-8")
    preexisting = get_existing_run_logs([old, new])
    assert preexisting == {old_log}

    # Simulate verification: write the mutants and the run log that verifying the new one creates.
    for path in (old, new):
        path.write_text("int bar(void) { return 1; }\n", encoding="utf-8")
    new_log.write_text("{}\n", encoding="utf-8")

    remove_mutants_and_run_logs([old, new], preexisting)
    assert not old.exists()
    assert not new.exists()
    assert old_log.exists()
    assert not new_log.exists()
