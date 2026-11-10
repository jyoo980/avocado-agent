"""Choose the include directories to pass to CBMC."""

import argparse
from pathlib import Path

from loguru import logger

from .paths import resolve_parent

AUTO_INCLUDE_HELP = (
    "For a C file <project>/src/<name>.c, pass <project>/include to CBMC as an include path "
    "if that directory exists. "
    "Fits projects whose include/ directory is a sibling of their src/ directory."
)
"""Help text for an `--auto-include` command-line option that is passed to `get_include_dirs`.

The text does not state the option's default, nor how the auto-detected directory is ordered
relative to other include directories; each client describes those.
"""

AUTO_INCLUDE_ENV_VAR = "AVOCADO_AUTO_INCLUDE"
"""The name of an environment variable that turns off auto-detection in `avocado-run-cbmc`.

`avocado-run-cbmc` passes `auto_include=False` to `get_include_dirs` if the variable's value is
`0`. `avocado-verify --no-auto-include` sets it to `0` for the agent's sessions, so that the
agent's runs use the same include directories as the harness's own runs.
"""


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

    When `auto_include` is true, the auto-detected directory is appended to `include_dirs`
    unless `include_dirs` already contains it. For a source file `<project>/src/<name>.c`, the
    auto-detected directory is `<project>/include`, if that directory exists. No directory is
    auto-detected for a source file whose parent directory is not named `src`.
    Many CMake projects keep public headers in `<project>/include/` while sources live in
    `<project>/src/`. When that layout holds, the auto-detected directory lets CBMC resolve
    `#include "foo.h"` without the caller having to configure paths by hand. Because the
    auto-detected directory follows the explicitly given ones, CBMC searches it after them.
    That ordering governs only the `-I` search path: for `#include "foo.h"`, the preprocessor
    first searches the directory of the file that contains the directive. For example, if
    `<project>/include/a.h` includes `"b.h"`, it gets `<project>/include/b.h` even when an
    explicitly given directory also contains a `b.h`.
    `<project>` is determined from the directory that contains `source_file`, without following
    `source_file` itself if it is a symlink, so a symlinked source file uses the `include/`
    directory of the project that contains the symlink. Symlinks and `..` in the directory are
    resolved as the filesystem resolves them.

    Args:
        source_file (str): Path to a `.c` file.
        include_dirs (list[str]): Include directories that were given explicitly, such as ones
            given on the command line. A relative path is relative to the current working
            directory.
        auto_include (bool): True iff the auto-detected directory, if any, should follow
            `include_dirs`.

    Returns:
        list[str]: A new list of include directories, made absolute with symlinks resolved and
            without duplicates (the first occurrence is kept): `include_dirs`, followed by the
            auto-detected directory if `auto_include` is true, that directory exists, and
            `include_dirs` does not already contain it.
    """
    detected = _autodetect_include_dir(source_file) if auto_include else None
    candidates = [*(Path(d).resolve() for d in include_dirs), detected]
    result = list(dict.fromkeys(str(d) for d in candidates if d is not None))
    if result:
        logger.info(f"[include-dirs] using {result}")
    return result


def _autodetect_include_dir(source_file: str) -> Path | None:
    """Return `<project>/include`, with symlinks resolved, for the given source file, if it exists.

    See `get_include_dirs` for how `<project>` is determined.

    Args:
        source_file (str): Path to a `.c` file `<project>/src/<name>.c`.

    Returns:
        Path | None: The resolved `<project>/include` directory, or None if it does not exist or
            the parent directory of `source_file` is not named `src`.
    """
    source_dir = resolve_parent(source_file).parent
    candidate = source_dir.parent / "include"
    return candidate.resolve() if source_dir.name == "src" and candidate.is_dir() else None
