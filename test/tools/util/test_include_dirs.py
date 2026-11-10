"""Tests for `tools.util.include_dirs`."""

import argparse
from pathlib import Path

import pytest

from tools.util import existing_include_dir, get_include_dirs


def _symlink_or_skip(link: Path, target: Path) -> None:
    """Make `link` a symlink to `target`, or skip the test if the platform does not permit it."""
    try:
        link.symlink_to(target)
    except OSError as e:
        pytest.skip(f"cannot create symlinks: {e}")


def test_existing_include_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "stubs").mkdir()
    (tmp_path / "file").write_text("")
    assert existing_include_dir("stubs") == "stubs"
    for bad in ["", "nonexistent", "file"]:
        with pytest.raises(argparse.ArgumentTypeError, match="Not an existing include directory"):
            existing_include_dir(bad)


def test_get_include_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    source = str(tmp_path / "src" / "foo.c")
    include = tmp_path / "include"
    detected = str(include.resolve())
    stubs = str(tmp_path.resolve() / "stubs")

    # No sibling `include/` directory.
    assert get_include_dirs(source, [], auto_include=True) == []
    assert get_include_dirs(source, [stubs], auto_include=True) == [stubs]

    include.mkdir()
    assert get_include_dirs(source, [], auto_include=False) == []
    assert get_include_dirs(source, [], auto_include=True) == [detected]
    assert get_include_dirs(source, [stubs], auto_include=False) == [stubs]
    assert get_include_dirs(source, [stubs], auto_include=True) == [detected, stubs]

    # A relative source file.
    assert get_include_dirs("src/foo.c", [], auto_include=True) == [detected]

    # The auto-detected directory is not duplicated.
    assert get_include_dirs(source, [detected], auto_include=True) == [detected]

    # Explicit directories are resolved against the current working directory and deduplicated.
    (tmp_path / "stubs").mkdir()
    assert get_include_dirs(
        source, ["stubs", "./stubs", "src/../stubs", stubs], auto_include=False
    ) == [stubs]
    assert get_include_dirs(source, ["include", "stubs"], auto_include=True) == [detected, stubs]
    _symlink_or_skip(tmp_path / "link", tmp_path / "stubs")
    assert get_include_dirs(source, ["link", "stubs"], auto_include=False) == [stubs]


def test_get_include_dirs_returns_new_list(tmp_path: Path) -> None:
    include_dirs = [str(tmp_path.resolve())]
    result = get_include_dirs(str(tmp_path / "src" / "foo.c"), include_dirs, auto_include=False)
    result.append("other")
    assert include_dirs == [str(tmp_path.resolve())]


def test_get_include_dirs_symlinked_source(tmp_path: Path) -> None:
    target = tmp_path / "elsewhere" / "src" / "foo.c"
    target.parent.mkdir(parents=True)
    target.write_text("")
    (tmp_path / "elsewhere" / "include").mkdir()
    source = tmp_path / "proj" / "src" / "foo.c"
    source.parent.mkdir(parents=True)
    _symlink_or_skip(source, target)
    include = tmp_path / "proj" / "include"
    include.mkdir()
    assert get_include_dirs(str(source), [], auto_include=True) == [str(include.resolve())]


def test_get_include_dirs_dotdot_after_symlinked_dir(tmp_path: Path) -> None:
    # `work/link/../src/foo.c` names `project/src/foo.c`, not `work/src/foo.c`.
    (tmp_path / "project" / "subdir").mkdir(parents=True)
    (tmp_path / "project" / "src").mkdir()
    include = tmp_path / "project" / "include"
    include.mkdir()
    (tmp_path / "work" / "src").mkdir(parents=True)
    (tmp_path / "work" / "include").mkdir()
    _symlink_or_skip(tmp_path / "work" / "link", tmp_path / "project" / "subdir")
    source = str(tmp_path / "work" / "link" / ".." / "src" / "foo.c")
    assert get_include_dirs(source, [], auto_include=True) == [str(include.resolve())]


def test_get_include_dirs_symlinked_include_dir(tmp_path: Path) -> None:
    headers = tmp_path / "headers"
    headers.mkdir()
    _symlink_or_skip(tmp_path / "include", headers)
    source = str(tmp_path / "src" / "foo.c")
    detected = str(headers.resolve())
    assert get_include_dirs(source, [], auto_include=True) == [detected]
    assert get_include_dirs(source, [str(headers)], auto_include=True) == [detected]
