"""What `avocado-verify` and its tests share about sessions, their copies and their verdicts.

`avocado-verify` gives each session a private copy of the source directory. This module says what
such a copy leaves out and what counts as an edit outside the source file, and it is the one place
that builds and reads the status of a vacuous proof and that says which functions a merge
rechecks.
"""

from __future__ import annotations

import filecmp
import shutil
from typing import TYPE_CHECKING

from eval.mutants.util import MUTANT_MARKERS
from tools.run_cbmc import VACUOUS_VERDICT

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from pathlib import Path

# Suffix of the directory beside the canonical file in which the harness keeps the transcripts of
# the sessions it had to stop at the session timeout.
TRANSCRIPTS_DIR_SUFFIX = "-avocado-transcripts"

# Suffix of the file beside the canonical file into which a merge writes the merged source before
# it atomically replaces the canonical file with it. The file exists only while a merge commits.
MERGE_STAGING_SUFFIX = ".avocado-merge"

# What is left out when the source directory is copied for a session or a ground-truth run, and
# what is not counted as an edit by the agent: the harness's and the tool's own logs, state and
# caches (including the compile-failure log the tool writes into whatever directory the agent runs
# it from), kept session transcripts, CBMC intermediates, mutant sources, the staging file of a
# merge that is committing, and the folder metadata macOS writes whenever a directory is opened in
# Finder. Each copy then starts clean and, in particular, gets its own
# `<stem>-verification-attempts.jsonl`.
SNAPSHOT_IGNORE = shutil.ignore_patterns(
    ".git",
    ".DS_Store",
    "*.goto",
    "*-callgraph.json",
    "*.jsonl",
    f"*{TRANSCRIPTS_DIR_SUFFIX}",
    *(f"*__{marker}_*.c" for marker in MUTANT_MARKERS),
    "mutation_compile_failures.log",
    f"*{MERGE_STAGING_SUFFIX}",
)


def vacuous_status(vacuity: str | None) -> str:
    """Return the status of a function whose proof is vacuous.

    This is the one place that builds the status, and `is_vacuous_status` the one place that
    reads it, so the two cannot drift apart.

    Args:
        vacuity (str | None): The kind of vacuity, if known.

    Returns:
        str: `VACUOUS (<vacuity>)`, or `VACUOUS` when the kind is not known.
    """
    return f"{VACUOUS_VERDICT} ({vacuity})" if vacuity else VACUOUS_VERDICT


def is_vacuous_status(why: str) -> bool:
    """Return whether a function's status says its proof is vacuous.

    Args:
        why (str): A status as the harness's merge state holds it, or a verdict from the run log.

    Returns:
        bool: True iff it is one `vacuous_status` builds.
    """
    return why.startswith(VACUOUS_VERDICT)


def rechecked_on_change(verified: Iterable[str], unverified: Mapping[str, str]) -> set[str]:
    """Return the functions to re-verify when a merge changes one of their callees' contracts.

    Those are the verified functions and the ones whose proof is vacuous. A vacuous proof counts
    as verified in the final tally, so it must not keep a verdict that the new contract has made
    stale; but only the verified ones are protected by rule 9 (the harness's merge reports only
    the verified ones as broken).

    Args:
        verified (Iterable[str]): Functions whose latest ground truth on the canonical file
            passed.
        unverified (Mapping[str, str]): Functions whose latest ground truth did not pass, each
            with why.

    Returns:
        set[str]: The functions.
    """
    return set(verified) | {name for name, why in unverified.items() if is_vacuous_status(why)}


def find_stray_files(session_file: Path, canonical_file: Path, fork_base: bytes) -> list[str]:
    """Return the files other than the source file that were added, changed or deleted in a copy.

    Headers, other sources and new files in the private copy are never merged. A file is compared
    to the canonical directory's copy of it, which no session writes; logs, caches, CBMC
    intermediates and mutant sources (`SNAPSHOT_IGNORE`), and everything inside a directory that
    `SNAPSHOT_IGNORE` leaves out, do not count. A file of the canonical directory that the copy
    no longer has was deleted from the copy, so the session might have verified code that ground
    truth will not see.

    The exception is a file whose canonical copy is the canonical source file itself, such as the
    target `foo_impl.c` of a canonical source file `foo.c -> foo_impl.c`. Merges of other sessions
    write that file, so it is compared to `fork_base` instead.

    Args:
        session_file (Path): The session's private copy of the source file.
        canonical_file (Path): The canonical source file, in the canonical source directory.
        fork_base (bytes): The contents of `canonical_file` when the session copy was made.

    Returns:
        list[str]: Relative paths tagged `added:`, `modified:` or `deleted:`, in sorted order.
    """
    stray: list[str] = []
    session_dir = session_file.parent
    canonical_dir = canonical_file.parent
    session_relative = session_file.relative_to(session_dir)
    for relative in sorted(_snapshot_files(session_dir)):
        if relative == session_relative:
            continue
        path = session_dir / relative
        original = canonical_dir / relative
        if not original.is_file():
            stray.append(f"added:{relative}")
        elif (
            path.read_bytes() != fork_base
            if original.samefile(canonical_file)
            else not filecmp.cmp(original, path, shallow=False)
        ):
            stray.append(f"modified:{relative}")
    stray.extend(
        f"deleted:{relative}"
        for relative in sorted(_snapshot_files(canonical_dir))
        if relative != session_relative and not (session_dir / relative).is_file()
    )
    return sorted(stray, key=lambda entry: entry.partition(":")[2])


def _snapshot_files(directory: Path) -> list[Path]:
    """Return the files under `directory` that a snapshot copies, relative to `directory`.

    A file is left out when `SNAPSHOT_IGNORE` leaves out its name or the name of any directory
    between `directory` and it, as `shutil.copytree` does when it copies a snapshot.

    Args:
        directory (Path): A source directory or a session's copy of one.

    Returns:
        list[Path]: Relative paths, in no particular order.
    """
    files: list[Path] = []
    for path in directory.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(directory)
        parent = directory
        for part in relative.parts:
            if SNAPSHOT_IGNORE(str(parent), [part]):
                break
            parent = parent / part
        else:
            files.append(relative)
    return files
