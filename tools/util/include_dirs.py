"""Choose the include directories to pass to CBMC."""

import argparse
from pathlib import Path

from loguru import logger

from .paths import resolve_parent

AUTO_INCLUDE_HELP = (
    "For each .c file <project>/<subdir>/<name>.c, pass <project>/include to CBMC "
    "as an include path if that directory exists. "
    "Fits projects whose include/ directory is a sibling of their src/ directory."
)
"""Help text for an `--auto-include` command-line option that is passed to `get_include_dirs`."""


def existing_include_dir(value: str) -> str:
    """Return `value` if it names an existing directory; for use as an argparse `type=`.

    Args:
        value (str): A command-line argument, such as `include` or `/usr/local/include`. A
            relative path is relative to the current working directory.

    Returns:
        str: `value`, unchanged.

    Raises:
        argparse.ArgumentTypeError: If `value` is empty or is not an existing directory.
    """
    if not value or not Path(value).is_dir():
        msg = f"Not an existing include directory: {value!r}"
        raise argparse.ArgumentTypeError(msg)
    return value


def get_include_dirs(source_file: str, include_dirs: list[str], *, auto_include: bool) -> list[str]:
    """Return the include directories with which to verify `source_file`, and log them.

    When `auto_include` is true, the result starts with the auto-detected directory: for a source
    file `<project>/<subdir>/<name>.c`, that is `<project>/include`, if that directory exists.
    Many CMake projects keep public headers in `<project>/include/` while sources live in
    `<project>/src/`. When that layout holds, the auto-detected directory lets CBMC resolve
    `#include "foo.h"` without the caller having to configure paths by hand. `<project>` is
    determined from the directory that contains `source_file`, without following `source_file`
    itself if it is a symlink, so a symlinked source file uses the `include/` directory of the
    project that contains the symlink. Symlinks and `..` in the directory are resolved as the
    filesystem resolves them.

    Args:
        source_file (str): Path to a `.c` file.
        include_dirs (list[str]): Include directories that were given explicitly, such as ones
            given on the command line. A relative path is relative to the current working
            directory.
        auto_include (bool): True iff the auto-detected directory, if any, should precede
            `include_dirs`.

    Returns:
        list[str]: A new list of include directories, made absolute with symlinks resolved and
            without duplicates (the first occurrence is kept): the auto-detected directory (if
            `auto_include` is true and that directory exists), followed by `include_dirs`.
    """
    detected = _autodetect_include_dir(source_file) if auto_include else None
    candidates = [detected, *(Path(d).resolve() for d in include_dirs)]
    result = list(dict.fromkeys(str(d) for d in candidates if d is not None))
    if result:
        logger.info(f"[include-dirs] using {result}")
    return result


def _autodetect_include_dir(source_file: str) -> Path | None:
    """Return `<project>/include`, with symlinks resolved, for the given source file, if it exists.

    See `get_include_dirs` for how `<project>` is determined.

    Args:
        source_file (str): Path to a `.c` file `<project>/<subdir>/<name>.c`.

    Returns:
        Path | None: The resolved `<project>/include` directory, or None if it does not exist.
    """
    candidate = resolve_parent(source_file).parent.parent / "include"
    return candidate.resolve() if candidate.is_dir() else None
