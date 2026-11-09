"""Choose the include directories to pass to CBMC."""

from pathlib import Path

from loguru import logger

from .paths import autodetect_include_dirs

AUTO_INCLUDE_HELP = (
    "For a C file <project>/src/<name>.c, pass <project>/include to CBMC as an include path "
    "if that directory exists. "
    "Fits projects whose include/ directory is a sibling of their src/ directory."
)
"""Help text for an `--auto-include` command-line option that is passed to `get_include_dirs`.

The text does not state the option's default, nor how the auto-detected directory is ordered
relative to other include directories; each client describes those.
"""


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
        include_dirs (list[str]): Include directories that were given explicitly, as returned by
            `resolve_include_dirs()`.
        auto_include (bool): True iff the auto-detected directory, if any, should follow
            `include_dirs`.

    Returns:
        list[str]: A new list of absolute include directories, without duplicates:
            `include_dirs`, followed by the auto-detected directory if `auto_include` is true, that
            directory exists, and `include_dirs` does not already contain it.
    """
    result = list(include_dirs)
    if auto_include:
        # Resolve the detected directory as `resolve_include_dirs` resolves `include_dirs`, so that
        # a duplicate is recognized.
        detected = [str(Path(d).resolve()) for d in autodetect_include_dirs(source_file)]
        result = list(dict.fromkeys([*result, *detected]))
    if result:
        logger.debug(f"[include-dirs] using {result}")
    return result
