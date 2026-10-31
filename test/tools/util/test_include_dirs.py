"""Tests for `tools.util.include_dirs`."""

from pathlib import Path

import pytest

from tools.util import get_include_dirs


def test_get_include_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    source = str(tmp_path / "src" / "foo.c")
    include = tmp_path / "include"
    detected = str(include.resolve())
    stubs = str((tmp_path / "stubs").resolve())

    # No sibling `include/` directory.
    assert get_include_dirs(source, [], auto_include=True) == []
    assert get_include_dirs(source, ["stubs"], auto_include=True) == [stubs]

    include.mkdir()
    assert get_include_dirs(source, [], auto_include=False) == []
    assert get_include_dirs(source, [], auto_include=True) == [detected]
    assert get_include_dirs(source, ["stubs"], auto_include=False) == [stubs]
    assert get_include_dirs(source, ["stubs"], auto_include=True) == [detected, stubs]

    # A relative source file.
    assert get_include_dirs("src/foo.c", [], auto_include=True) == [detected]

    # Duplicates are removed.
    assert get_include_dirs(source, [str(include)], auto_include=True) == [detected]
    assert get_include_dirs(source, ["stubs", "./stubs"], auto_include=False) == [stubs]
    assert get_include_dirs(source, ["stubs", "src/../stubs"], auto_include=False) == [stubs]
    assert get_include_dirs(source, ["stubs", stubs], auto_include=False) == [stubs]


def test_get_include_dirs_symlink(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    source = str(tmp_path / "src" / "foo.c")
    assert get_include_dirs(source, [str(link), str(real)], auto_include=False) == [
        str(real.resolve())
    ]


def test_get_include_dirs_returns_new_list(tmp_path: Path) -> None:
    include_dirs = [str(tmp_path)]
    result = get_include_dirs(str(tmp_path / "src" / "foo.c"), include_dirs, auto_include=False)
    result.append("other")
    assert include_dirs == [str(tmp_path)]
