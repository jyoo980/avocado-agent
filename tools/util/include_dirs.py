"""Helpers for determining the C preprocessor's include search path."""

from pathlib import Path


def autodetect_include_dirs(source_file: str) -> list[str]:
    """Return `[<source>/../include]` if that directory exists, else an empty list.

    Many CMake projects keep public headers in `<project>/include/` while sources live in
    `<project>/src/`. When that layout holds, returning the sibling `include/` directory lets
    CBMC resolve `#include "foo.h"` without the caller having to configure paths by hand.

    Args:
        source_file (str): a path to a `.c` file, such as `<project>/src/foo.c`.

    Returns:
        list[str]: `[<resolved include dir>]` when present, else `[]`.
    """
    candidate = Path(source_file).resolve().parent.parent / "include"
    return [str(candidate)] if candidate.is_dir() else []
