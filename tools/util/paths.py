"""Utilities for file paths."""

from pathlib import Path


def resolve_parent(path: str) -> Path:
    """Return `path` made absolute, with symlinks resolved in its parent but not in its last part.

    If `path` is a symlink, the result is the symlink itself rather than its target. Therefore, a
    tool that compiles a symlinked C file through the result, or writes a file next to the result,
    sees the quoted `#include`s of the directory that contains the symlink, as a compiler given the
    symlink would. A `..` in the parent is resolved by the filesystem, after following any symlink
    that precedes it. In contrast, `os.path.abspath` collapses `..` lexically:
    `/work/link/../src/foo.c`, where `/work/link` is a symlink to `/project/subdir`, names
    `/project/src/foo.c`, but `os.path.abspath` yields `/work/src/foo.c`.

    Args:
        path (str): A path to a file, which need not exist. A relative path is relative to the
            current working directory.

    Returns:
        Path: The absolute path, whose parent has no symlinks and no `.` or `..` parts.
    """
    absolute = Path(path).absolute()
    return absolute.parent.resolve() / absolute.name


def autodetect_include_dirs(source_file: str) -> list[str]:
    """Return `[<source>/../include]` if that directory exists, else an empty list.

    Many CMake projects keep public headers in `<project>/include/` while sources live in
    `<project>/src/`. When that layout holds, returning the sibling `include/` directory lets
    CBMC resolve `#include "foo.h"` without the caller having to configure paths by hand.

    `<source>` is the directory that contains `source_file`, without following `source_file`
    itself if it is a symlink, so a symlinked source file uses the `include/` directory of the
    project that contains the symlink.

    Args:
        source_file (str): Path to a `.c` file.

    Returns:
        list[str]: `[<resolved include dir>]` when present, else `[]`.
    """
    candidate = resolve_parent(source_file).parent.parent / "include"
    return [str(candidate)] if candidate.is_dir() else []
