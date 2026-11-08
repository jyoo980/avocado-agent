"""Utilities for mutation testing."""

import hashlib
import os
from collections.abc import Callable
from pathlib import Path

from tools.run_cbmc import RunCbmcResult

# 0 indicates success, 10 indicates failure, 124 indicates timeout.
_CBMC_RETURN_CODES_SUCCESS_FAILURE_TIMEOUT = frozenset({0, 10, 124})

# The per-component file name limit (in bytes) on common filesystems (ext4, APFS, NTFS, ...).
_MAX_FILE_NAME_BYTES = 255


def get_bounded_mutant_file_name(
    stem: str, marker: str, function: str, index: int, suffix: str
) -> str:
    """Return a mutant file name `<stem>__<marker>_<function>_<index><suffix>`, bounded in length.

    If the readable name would exceed the filesystem's per-component length limit, the function
    name is replaced by a short digest of the full (stem, function) pair, and the stem is truncated
    if needed. The name then stays bounded but is still distinct for each function and index. It
    always contains `__<marker>_`, so it still matches the patterns that `make clean-mutants`
    removes.

    Byte lengths are measured with `os.fsencode`, which counts name bytes the way the filesystem
    does. It uses surrogateescape, so basenames that are not valid UTF-8 do not raise.

    Args:
        stem (str): The stem of the original source file.
        marker (str): The mutant-kind marker, e.g. `mutant` or `clause_drop`.
        function (str): The function the mutant belongs to.
        index (int): The index of the mutant.
        suffix (str): The suffix of the original source file, e.g. `.c`.

    Returns:
        str: The mutant file name (a single path component).
    """
    name = f"{stem}__{marker}_{function}_{index}{suffix}"
    if len(os.fsencode(name)) <= _MAX_FILE_NAME_BYTES:
        return name

    # Too long: replace the function name with a digest of the full (stem, function) pair, so that
    # truncating the stem cannot make two distinct inputs collide.
    digest = hashlib.sha256(os.fsencode(f"{stem}\0{function}")).hexdigest()[:16]
    tail = f"__{marker}_{digest}_{index}{suffix}"
    stem_budget = max(0, _MAX_FILE_NAME_BYTES - len(os.fsencode(tail)))
    # Truncate at character boundaries so that a multibyte character is never split.
    truncated_stem = ""
    used = 0
    for char in stem:
        char_bytes = len(os.fsencode(char))
        if used + char_bytes > stem_budget:
            break
        truncated_stem += char
        used += char_bytes
    return truncated_stem + tail


def get_mutant_paths(
    path_to_original_source: Path, get_path_for_index: Callable[[int], Path], count: int
) -> list[Path]:
    """Return `count` distinct paths to which to write the mutants of the given source file.

    The paths are those of `get_path_for_index` for increasing indices, except that an index is
    skipped if its path already exists and is the original source file or (if the original source
    file is a symlink) its target. Writing a mutant there would overwrite the source, and cleanup
    would delete it. For example, if `foo.c` is a symlink to `foo__mutant_bar_0.c` in the same
    directory, then the index 0 is skipped for the mutants of `bar`.

    Args:
        path_to_original_source (Path): A path to an original source file.
        get_path_for_index (Callable[[int], Path]): Maps a mutant index, such as 0, to the path for
            the mutant with that index, such as `/src/foo__mutant_bar_0.c`.
        count (int): The number of paths to return.

    Returns:
        list[Path]: The paths to which to write the mutants, in index order.
    """
    paths: list[Path] = []
    index = 0
    while len(paths) < count:
        path = get_path_for_index(index)
        index += 1
        if path.exists() and path.samefile(path_to_original_source):
            continue
        paths.append(path)
    return paths


def is_valid_mutation_candidate(run_cbmc_result: RunCbmcResult) -> bool:
    """Return True iff the function under verification can serve as a mutation-testing baseline.

    A valid candidate is one for which CBMC ran end-to-end and verified the function — i.e.,
    every pipeline step completed without failure or timeout and CBMC reported success.
    Mutants are scored against this baseline, so anything weaker (compile/instrument failure,
    timeout, or a real verification failure) makes the candidate unusable.

    Args:
        run_cbmc_result (RunCbmcResult): The CBMC run result for the candidate function.

    Returns:
        bool: True iff the candidate verifies successfully.
    """
    return run_cbmc_result.is_function_verified


def check_expected_cbmc_return_code(return_code: int):
    """Check whether the return code is indicates a verification success, failure, or timeout.

    Args:
        return_code (int): The return code to check for verification success, failure, or timeout.
    """
    if return_code not in _CBMC_RETURN_CODES_SUCCESS_FAILURE_TIMEOUT:
        msg = (
            f"Unexpected CBMC return code: {return_code}. "
            "See: https://diffblue.github.io/cbmc/exit__codes_8h.html"
        )
        raise RuntimeError(msg)


def get_files_with_extension(path_str: str, extension: str) -> list[Path]:
    """Return the path(s) to the file(s) with the given extension at the path.

    If the path resolves to a directory, recursively collect files.

    Args:
        path_str (str): Path to a file or directory.
        extension (str): The extension to match files on.

    Returns:
        list[Path]: The path(s) to the file(s) with the given extension at the path.
    """
    path = Path(path_str)
    if not path.exists():
        msg = f"{path} does not exist; double-check for full or relative paths"
        raise RuntimeError(msg)
    if path.is_dir():
        return sorted(path.rglob(f"*{extension}"))
    return [path] if path.suffix == extension else []
