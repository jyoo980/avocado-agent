"""Choose the include directories to pass to CBMC."""

import os
from pathlib import Path

from loguru import logger

AUTO_INCLUDE_HELP = (
    "For each .c file <project>/<subdir>/<name>.c, pass <project>/include to CBMC "
    "as an include path if that directory exists, after any explicitly given include directories. "
    "Fits projects whose include/ directory is a sibling of their src/ directory."
)
"""Help text for an `--auto-include` command-line option that is passed to `get_include_dirs`."""


def resolve_include_dirs(include_dirs: list[str]) -> list[str]:
    """Return `include_dirs`, made absolute with symlinks resolved, without duplicates.

    Relative paths are resolved against the current working directory. The first occurrence of a
    duplicate is kept.

    Args:
        include_dirs (list[str]): Include directories, such as ones given on the command line.

    Returns:
        list[str]: A new list of the resolved include directories, without duplicates.

    Raises:
        ValueError: If an element of `include_dirs` is empty or is not an existing directory.
    """
    for include_dir in include_dirs:
        if not include_dir or not Path(include_dir).is_dir():
            msg = f"Not an existing include directory: {include_dir!r}"
            raise ValueError(msg)
    return list(dict.fromkeys(str(Path(include_dir).resolve()) for include_dir in include_dirs))


def get_include_dirs(source_file: str, include_dirs: list[str], *, auto_include: bool) -> list[str]:
    """Return the include directories with which to verify `source_file`, and log them.

    When `auto_include` is true, the result ends with the auto-detected directory: for a source
    file `<project>/<subdir>/<name>.c`, that is `<project>/include`, if that directory exists.
    Explicitly given directories come first so that their headers (such as stubs) take
    precedence over the auto-detected ones.
    Many CMake projects keep public headers in `<project>/include/` while sources live in
    `<project>/src/`. When that layout holds, the auto-detected directory lets CBMC resolve
    `#include "foo.h"` without the caller having to configure paths by hand. `<project>` is
    determined from `source_file` as written, without following symlinks, so a symlinked source
    file uses the `include/` directory of the project that contains the symlink.

    Args:
        source_file (str): Path to a `.c` file.
        include_dirs (list[str]): Include directories that were given explicitly, as returned by
            `resolve_include_dirs()`.
        auto_include (bool): True iff the auto-detected directory, if any, should follow
            `include_dirs`.

    Returns:
        list[str]: A new list of absolute include directories, without duplicates:
            `include_dirs`, followed by the auto-detected directory (if `auto_include` is true and
            that directory exists).
    """
    result = list(include_dirs)
    if auto_include:
        # Unlike `Path.resolve`, `os.path.abspath` does not follow symlinks.
        detected = Path(os.path.abspath(source_file)).parent.parent / "include"  # ruff: ignore[os-path-abspath] - must not follow symlinks.
        if detected.is_dir():
            result = list(dict.fromkeys([*result, str(detected.resolve())]))
    if result:
        logger.debug(f"[include-dirs] using {result}")
    return result
