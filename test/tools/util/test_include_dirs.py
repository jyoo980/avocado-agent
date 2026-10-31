"""Tests for `tools.util.include_dirs`."""

from pathlib import Path

from tools.util import get_include_dirs


def test_get_include_dirs(tmp_path: Path) -> None:
    source = str(tmp_path / "src" / "foo.c")
    include = tmp_path / "include"
    detected = str(include.resolve())

    # No sibling `include/` directory.
    assert get_include_dirs(source, [], auto_include=True) == []
    assert get_include_dirs(source, ["stubs"], auto_include=True) == ["stubs"]

    include.mkdir()
    assert get_include_dirs(source, [], auto_include=False) == []
    assert get_include_dirs(source, [], auto_include=True) == [detected]
    assert get_include_dirs(source, ["stubs"], auto_include=False) == ["stubs"]
    assert get_include_dirs(source, ["stubs"], auto_include=True) == [detected, "stubs"]

    # Duplicates are removed.
    assert get_include_dirs(source, [str(include)], auto_include=True) == [detected]
    assert get_include_dirs(source, ["stubs", "./stubs"], auto_include=False) == ["stubs"]


def test_get_include_dirs_returns_new_list(tmp_path: Path) -> None:
    include_dirs = ["stubs"]
    result = get_include_dirs(str(tmp_path / "src" / "foo.c"), include_dirs, auto_include=False)
    result.append("other")
    assert include_dirs == ["stubs"]
