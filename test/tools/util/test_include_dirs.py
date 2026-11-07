"""Tests for `tools.util.include_dirs`."""

from pathlib import Path

from tools.util.include_dirs import autodetect_include_dirs


def test_autodetect_include_dirs_with_sibling_include(tmp_path: Path) -> None:
    """Test that a sibling `include/` directory of the source's directory is returned."""
    (tmp_path / "src").mkdir()
    (tmp_path / "include").mkdir()
    source = tmp_path / "src" / "f.c"
    source.touch()
    assert autodetect_include_dirs(str(source)) == [str((tmp_path / "include").resolve())]


def test_autodetect_include_dirs_without_sibling_include(tmp_path: Path) -> None:
    """Test that no directory is returned when there is no sibling `include/` directory."""
    (tmp_path / "src").mkdir()
    source = tmp_path / "src" / "f.c"
    source.touch()
    assert autodetect_include_dirs(str(source)) == []
