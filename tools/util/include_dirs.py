"""Choose the include directories to pass to CBMC."""

from pathlib import Path


def get_include_dirs(source_file: str, include_dirs: list[str], *, auto_include: bool) -> list[str]:
    """Return the include directories with which to verify `source_file`.

    When `auto_include` is true, the result starts with the auto-detected directory: for a source
    file `<project>/<subdir>/<name>.c`, that is `<project>/include`, if that directory exists.
    Many CMake projects keep public headers in `<project>/include/` while sources live in
    `<project>/src/`. When that layout holds, the auto-detected directory lets CBMC resolve
    `#include "foo.h"` without the caller having to configure paths by hand.

    The result contains no two entries that resolve to the same directory; the first occurrence
    is kept. The result is a new list, never `include_dirs` itself.

    Args:
        source_file (str): Path to a `.c` file.
        include_dirs (list[str]): Include directories that were given explicitly.
        auto_include (bool): True iff the auto-detected directory, if any, should precede
            `include_dirs`.

    Returns:
        list[str]: The include directories, the auto-detected one first.
    """
    candidates = list(include_dirs)
    if auto_include:
        detected = Path(source_file).resolve().parent.parent / "include"
        if detected.is_dir():
            candidates.insert(0, str(detected))
    result: list[str] = []
    seen: set[Path] = set()
    for include_dir in candidates:
        resolved = Path(include_dir).resolve()
        if resolved not in seen:
            seen.add(resolved)
            result.append(include_dir)
    return result
