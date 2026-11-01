"""Tests for `tools.util.include_dirs`."""

from pathlib import Path

import pytest

from tools.util import get_include_dirs, resolve_include_dirs


def _symlink_or_skip(link: Path, target: Path) -> None:
    """Make `link` a symlink to `target`, or skip the test if the platform does not permit it."""
    try:
        link.symlink_to(target)
    except OSError as e:
        pytest.skip(f"cannot create symlinks: {e}")


def test_resolve_include_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "src").mkdir()
    stubs_path = tmp_path / "stubs"
    stubs_path.mkdir()
    stubs = str(stubs_path.resolve())

    assert resolve_include_dirs([]) == []
    assert resolve_include_dirs(["stubs"]) == [stubs]
    assert resolve_include_dirs(["stubs", "./stubs", "src/../stubs", stubs]) == [stubs]

    _symlink_or_skip(tmp_path / "link", stubs_path)
    assert resolve_include_dirs(["link", "stubs"]) == [stubs]


def test_resolve_include_dirs_rejects_bad_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "file").write_text("")
    for bad in ["", "nonexistent", "file"]:
        with pytest.raises(ValueError, match="Not an existing include directory"):
            resolve_include_dirs([bad])


def test_resolve_include_dirs_returns_new_list(tmp_path: Path) -> None:
    include_dirs = [str(tmp_path.resolve())]
    result = resolve_include_dirs(include_dirs)
    result.append("other")
    assert include_dirs == [str(tmp_path.resolve())]


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
    assert get_include_dirs(source, [stubs], auto_include=True) == [stubs, detected]

    # A relative source file.
    assert get_include_dirs("src/foo.c", [], auto_include=True) == [detected]

    # The auto-detected directory is not duplicated.
    assert get_include_dirs(source, [detected], auto_include=True) == [detected]
    assert get_include_dirs(source, [detected, stubs], auto_include=True) == [detected, stubs]


def test_get_include_dirs_requires_src(tmp_path: Path) -> None:
    (tmp_path / "include").mkdir()
    for subdir in ["lib", "scratch", "src-old"]:
        source = str(tmp_path / subdir / "foo.c")
        assert get_include_dirs(source, [], auto_include=True) == []


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
