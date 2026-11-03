"""Utilities for file paths."""

from pathlib import Path


def resolve_parent(path: str) -> Path:
    """Return `path` made absolute, with symlinks resolved in its parent but not in its last part.

    If `path` is a symlink, the result is the symlink itself rather than its target. A `..` in the
    parent is resolved by the filesystem, after following any symlink that precedes it. In
    contrast, `os.path.abspath` collapses `..` lexically: `/work/link/../src/foo.c`, where
    `/work/link` is a symlink to `/project/subdir`, names `/project/src/foo.c`, but
    `os.path.abspath` yields `/work/src/foo.c`.

    Args:
        path (str): A path to a file, which need not exist.

    Returns:
        Path: The absolute path, whose parent has no symlinks and no `.` or `..` parts.
    """
    absolute = Path(path).absolute()
    return absolute.parent.resolve() / absolute.name
