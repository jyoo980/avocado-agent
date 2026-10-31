"""Choose the include directories to pass to CBMC."""

from pathlib import Path

AUTO_INCLUDE_HELP = (
    "For each .c file <project>/<subdir>/<name>.c, pass <project>/include to CBMC "
    "as an include path if that directory exists. "
    "Fits projects whose include/ directory is a sibling of their src/ directory."
)
"""Help text for an `--auto-include` command-line option that is passed to `get_include_dirs`."""


def get_include_dirs(source_file: str, include_dirs: list[str], *, auto_include: bool) -> list[str]:
    """Return the include directories with which to verify `source_file`.

    When `auto_include` is true, the result starts with the auto-detected directory: for a source
    file `<project>/<subdir>/<name>.c`, that is `<project>/include`, if that directory exists.
    Many CMake projects keep public headers in `<project>/include/` while sources live in
    `<project>/src/`. When that layout holds, the auto-detected directory lets CBMC resolve
    `#include "foo.h"` without the caller having to configure paths by hand.

    Every directory in the result is absolute, with symlinks resolved; relative paths are resolved
    against the current working directory. Absolute paths remain valid when CBMC runs in a
    different working directory. The result contains no duplicates; the first occurrence is kept.

    Args:
        source_file (str): Path to a `.c` file.
        include_dirs (list[str]): Include directories that were given explicitly.
        auto_include (bool): True iff the auto-detected directory, if any, should precede
            `include_dirs`.

    Returns:
        list[str]: A new list of the resolved include directories, without duplicates: the
            auto-detected directory (if `auto_include` is true and that directory exists),
            followed by `include_dirs`.
    """
    candidates = [Path(include_dir).resolve() for include_dir in include_dirs]
    if auto_include:
        detected = Path(source_file).resolve().parent.parent / "include"
        if detected.is_dir():
            candidates.insert(0, detected)
    return [str(include_dir) for include_dir in dict.fromkeys(candidates)]
