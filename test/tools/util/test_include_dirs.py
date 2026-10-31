"""Tests for `tools.util.include_dirs`."""

from pathlib import Path

from tools.util import autodetect_include_dirs, get_include_dirs


def test_autodetect_include_dirs(tmp_path: Path) -> None:
    source = tmp_path / "src" / "foo.c"
    source.parent.mkdir()
    source.write_text("int foo(void) { return 0; }\n")
    assert autodetect_include_dirs(str(source)) == []
    (tmp_path / "include").mkdir()
    assert autodetect_include_dirs(str(source)) == [str((tmp_path / "include").resolve())]


def test_get_include_dirs(tmp_path: Path) -> None:
    source = tmp_path / "src" / "foo.c"
    source.parent.mkdir()
    source.write_text("int foo(void) { return 0; }\n")
    (tmp_path / "include").mkdir()
    detected = str((tmp_path / "include").resolve())
    assert get_include_dirs(str(source), ["stubs"], auto_include=False) == ["stubs"]
    assert get_include_dirs(str(source), ["stubs"], auto_include=True) == [detected, "stubs"]
