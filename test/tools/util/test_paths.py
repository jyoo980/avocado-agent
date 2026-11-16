"""Tests for `tools.util.paths`."""

from pathlib import Path

import pytest

from tools.util import resolve_parent


def test_resolve_parent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path.resolve()
    (root / "project" / "subdir").mkdir(parents=True)
    (root / "project" / "src").mkdir()
    target = root / "project" / "src" / "target.c"
    target.write_text("")
    try:
        (root / "link").symlink_to(root / "project" / "subdir")
        (root / "project" / "src" / "foo.c").symlink_to(target)
    except OSError as e:
        pytest.skip(f"cannot create symlinks: {e}")

    foo = root / "project" / "src" / "foo.c"
    # `..` is resolved after following the directory symlink that precedes it.
    assert resolve_parent(str(root / "link" / ".." / "src" / "foo.c")) == foo
    # The last part is not followed, even if it is a symlink.
    assert resolve_parent(str(foo)) == foo
    # A relative path is resolved against the current working directory.
    monkeypatch.chdir(root / "project")
    assert resolve_parent("src/./foo.c") == foo
    # The file need not exist.
    assert resolve_parent("src/missing.c") == root / "project" / "src" / "missing.c"
