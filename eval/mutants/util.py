"""Utilities for mutation testing."""

import hashlib
import os
import re
from collections.abc import Callable
from itertools import count
from pathlib import Path

from tools.construct_call_graph import CALL_GRAPH_FILE_SUFFIX
from tools.run_cbmc import RunCbmcResult

# 0 indicates success, 10 indicates failure, 124 indicates timeout.
_CBMC_RETURN_CODES_SUCCESS_FAILURE_TIMEOUT = frozenset({0, 10, 124})

# The per-component file name limit (in bytes) on common filesystems (ext4, APFS, NTFS, ...).
_MAX_FILE_NAME_BYTES = 255

# The marker in the names of the source files that mutation testing generates.
MUTANT_MARKER = "mutant"
# The marker in the names of the source files that clause-redundancy scoring generates.
CLAUSE_DROP_MARKER = "clause_drop"
# The markers in the names of generated mutant source files, as passed to
# `get_bounded_mutant_file_name()`. The globs in the Makefile's `clean-mutants` target repeat them.
MUTANT_MARKERS = (MUTANT_MARKER, CLAUSE_DROP_MARKER)

# Matches the stem of a generated mutant source file: `<stem>__<marker>_[<function>_]<index>`,
# optionally followed by `-<attempt>` (see `get_bounded_mutant_file_name()`), where the marker is
# one of `MUTANT_MARKERS`. The function is optional, so a stem such as `foo__mutant_3` also
# matches.
_MUTANT_ARTIFACT_STEM_RE = re.compile(
    rf"__(?:{'|'.join(map(re.escape, MUTANT_MARKERS))})_(?:.+_)?\d+(?:-\d+)?$"
)


def is_mutant_artifact(path: Path) -> bool:
    """Return True if the path names a generated mutant source file.

    Mutation testing and clause-redundancy scoring generate such files.

    Args:
        path (Path): A path, such as `src/foo__mutant_bar_3.c`.

    Returns:
        bool: True if the path names a generated mutant source file.
    """
    return _MUTANT_ARTIFACT_STEM_RE.search(path.stem) is not None


def get_mutant_file_stem(path_to_original_source: Path) -> str:
    """Return the stem from which to build the names of the mutants of a source file.

    For a file that is not a symlink, this is its stem: `foo` for `foo.c`. For a symlink, the stem
    of its target is appended if it differs, as in `a__foo` for `a.c -> ../x/foo.c`. So:

     * The mutants of distinct files in one directory usually get distinct names, even if they
       are symlinks whose targets have the same name as each other or as a regular sibling file.
       Names can still coincide, as for `a.c -> foo.c` and a regular file `a__foo.c`, and two runs
       on one file use the same names; `create_mutant_file` therefore never reuses an existing
       file.
     * A mutant file name is never the name of the target, because its stem is longer than the
       target's stem. For example, for `foo.c -> foo__mutant_bar_0.c`, the mutant name
       `foo__foo__mutant_bar_0__mutant_bar_0.c` is not `foo__mutant_bar_0.c`.

    Args:
        path_to_original_source (Path): The path to the original source file.

    Returns:
        str: The stem from which to build mutant file names.
    """
    stem = path_to_original_source.stem
    if not path_to_original_source.is_symlink():
        return stem
    target_stem = path_to_original_source.resolve().stem
    return stem if target_stem == stem else f"{stem}__{target_stem}"


def create_mutant_file(path_for_attempt: Callable[[int], Path], content: str) -> Path:
    """Create a new file with the given content, without overwriting any existing file.

    The file is created at `path_for_attempt(0)`; if that file already exists -- left by another
    run, perhaps one that is still running, or a sibling source file's mutant with the same name
    -- at `path_for_attempt(1)`, and so on. Since the returned file was created by this call, the
    caller may delete it when done without deleting a file that it does not own.

    Args:
        path_for_attempt (Callable[[int], Path]): Returns the path to try for an attempt number;
            distinct numbers must give distinct paths.
        content (str): The content of the file, written as UTF-8.

    Returns:
        Path: The path of the file that was created.
    """
    for attempt in count():
        path = path_for_attempt(attempt)
        try:
            file = path.open("x", encoding="utf-8")
        except FileExistsError:
            continue
        try:
            with file:
                file.write(content)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return path
    msg = "Unreachable: count() is infinite."
    raise AssertionError(msg)


def get_bounded_mutant_file_name(
    stem: str, marker: str, function: str, index: int, suffix: str, attempt: int = 0
) -> str:
    """Return a mutant file name `<stem>__<marker>_<function>_<index><suffix>`, bounded in length.

    For a nonzero `attempt` (see `create_mutant_file`), `<index>` is followed by `-<attempt>`, as
    in `foo__mutant_bar_0-1.c`.

    If the readable name, or the name of the call graph that `construct_call_graph` caches for it,
    would exceed the filesystem's per-component length limit, the function name is replaced by a
    short digest of the full (stem, function) pair, and the stem is truncated if needed. The name
    then stays bounded but is still distinct for each function, index and attempt. It always
    contains `__<marker>_`, so it still matches the patterns that `make clean-mutants` removes,
    and `is_mutant_artifact()` recognizes it.

    Byte lengths are measured with `os.fsencode`, which counts name bytes the way the filesystem
    does. It uses surrogateescape, so basenames that are not valid UTF-8 do not raise.

    Args:
        stem (str): The stem of the original source file.
        marker (str): The mutant-kind marker, e.g. `mutant` or `clause_drop`.
        function (str): The function the mutant belongs to.
        index (int): The index of the mutant.
        suffix (str): The suffix of the original source file, e.g. `.c`.
        attempt (int): The attempt number; see `create_mutant_file`.

    Returns:
        str: The mutant file name (a single path component).
    """
    # Verifying a mutant may cache its call graph next to it, under a name in which the call-graph
    # suffix replaces `suffix`; that name must fit too.
    max_name_bytes = _MAX_FILE_NAME_BYTES - max(
        0, len(CALL_GRAPH_FILE_SUFFIX) - len(os.fsencode(suffix))
    )
    index_part = f"{index}-{attempt}" if attempt else str(index)
    name = f"{stem}__{marker}_{function}_{index_part}{suffix}"
    if len(os.fsencode(name)) <= max_name_bytes:
        return name

    # Too long: replace the function name with a digest of the full (stem, function) pair, so that
    # truncating the stem cannot make two distinct inputs collide.
    digest = hashlib.sha256(os.fsencode(f"{stem}\0{function}")).hexdigest()[:16]
    tail = f"__{marker}_{digest}_{index_part}{suffix}"
    stem_budget = max(0, max_name_bytes - len(os.fsencode(tail)))
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

    If the path resolves to a directory, recursively collect files, skipping generated mutant
    sources (see `is_mutant_artifact()`), such as those retained by `--keep-artifacts`.

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
        return sorted(p for p in path.rglob(f"*{extension}") if not is_mutant_artifact(p))
    return [path] if path.suffix == extension else []
