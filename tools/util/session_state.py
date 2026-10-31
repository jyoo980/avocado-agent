"""What `avocado-verify` and its tests share about sessions, their copies and their verdicts.

`avocado-verify` gives each session a private copy of the source directory. This module says what
such a copy leaves out and what counts as an edit outside the source file, and it is the one place
that builds and reads the status of a vacuous proof and that says which functions a merge
rechecks.
"""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

from tools.run_cbmc import VACUOUS_VERDICT

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from pathlib import Path

# Suffix of the directory beside the canonical file in which the harness keeps the transcripts of
# the sessions it had to stop at the session timeout.
TRANSCRIPTS_DIR_SUFFIX = "-avocado-transcripts"

# What is left out when the source directory is copied for a session or a ground-truth run, and
# what is not counted as an edit by the agent: the harness's and the tool's own logs, state and
# caches (including the compile-failure log the tool writes into whatever directory the agent runs
# it from), kept session transcripts, CBMC intermediates, mutant sources, and the folder metadata
# macOS writes whenever a directory is opened in Finder. Each copy then starts clean and, in
# particular, gets its own `<stem>-verification-attempts.jsonl`.
SNAPSHOT_IGNORE = shutil.ignore_patterns(
    ".git",
    ".DS_Store",
    "*.goto",
    "*-callgraph.json",
    "*.jsonl",
    f"*{TRANSCRIPTS_DIR_SUFFIX}",
    "*__mutant_*.c",
    "*__clause_drop_*.c",
    "mutation_compile_failures.log",
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


def find_stray_files(session_file: Path, canonical_dir: Path) -> list[str]:
    """Return the files other than the source file that were added or changed in a session copy.

    Headers, other sources and new files in the private copy are never merged. A file is compared
    to the canonical directory's copy of it, which no session ever writes; logs, caches, CBMC
    intermediates and mutant sources (`SNAPSHOT_IGNORE`) do not count.

    Args:
        session_file (Path): The session's private copy of the source file.
        canonical_dir (Path): The canonical source directory.

    Returns:
        list[str]: Relative paths tagged `added:` or `modified:`, in sorted order.
    """
    stray: list[str] = []
    session_dir = session_file.parent
    for path in sorted(session_dir.rglob("*")):
        if not path.is_file() or path == session_file:
            continue
        if SNAPSHOT_IGNORE(str(path.parent), [path.name]):
            continue
        relative = path.relative_to(session_dir)
        original = canonical_dir / relative
        if not original.is_file():
            stray.append(f"added:{relative}")
        elif original.read_bytes() != path.read_bytes():
            stray.append(f"modified:{relative}")
    return stray
