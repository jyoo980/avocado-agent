"""Saves a call graph to a file and prints the file name.

This script expects a single C source file as input, and computes a call graph based on the
functions that are immediately available in the file. For example, assume a function `a` calls `b`
and `c`, where `b` is defined in the same file but `c` is not (e.g., a library function, an
unresolved external function, etc). The entry for `a` in the call graph will list `b` as an
`internal` callee and `c` as an external callee.

See util/callgraph.py for more information about the structure of the call graph.

Usage:
    % avocado-construct-call-graph <PATH_TO_C_FILE>
"""

import argparse
import json
import os
import threading
import uuid
from pathlib import Path

from tools.util import get_call_graph

# `construct_call_graph` caches its JSON next to the source and is called from concurrent code
# paths (e.g. the evaluation driver scoring several functions of one file at once). The lock keeps
# threads of one process from parsing the same file redundantly (tree-sitter parsing is not
# thread-safe); the atomic rename in `construct_call_graph` keeps other processes from reading a
# partially written file.
_CALL_GRAPH_LOCK = threading.Lock()


def _read_umask() -> int:
    """Return the process's umask.

    `os.umask` can only be read by setting it, so this briefly changes it; it is called once, at
    import time, before any threads that might create files concurrently.

    Returns:
        int: The process's umask.
    """
    umask = os.umask(0o077)
    os.umask(umask)
    return umask


# The mode that `open` gives a newly created file: 0666 filtered by the umask.
_DEFAULT_FILE_MODE = 0o666 & ~_read_umask()


def main() -> None:
    """Construct a call graph comprising functions in a given file."""
    parser = argparse.ArgumentParser(
        description="Parse a C file and write a call graph JSON next to it."
    )
    parser.add_argument(
        "path_to_file_to_verify",
        help="Path to the C file from which to construct a call graph.",
    )
    args = parser.parse_args()
    print(construct_call_graph(args.path_to_file_to_verify))


def construct_call_graph(
    path_to_file_to_verify: str,
) -> str:
    """Construct a call graph for the given C file.

    The call graph is cached beside `path_to_file_to_verify` (without following symlinks). A cached
    graph is reused only if it is at least as new as the source file and, when
    `path_to_file_to_verify` is a symlink, the link itself; so editing the source or repointing the
    link causes the graph to be regenerated.

    Args:
        path_to_file_to_verify (str): The path to the file from which to construct a call graph.

    Returns:
        str: The path to the file to which the call graph is written.
    """
    source_path = Path(path_to_file_to_verify)
    path_to_call_graph = source_path.with_name(f"{source_path.stem}-callgraph.json")
    with _CALL_GRAPH_LOCK:
        if path_to_call_graph.exists() and _is_cache_fresh(path_to_call_graph, source_path):
            return str(path_to_call_graph)
        call_graph = get_call_graph(path_to_file_to_verify)
        # Write to a uniquely named sibling and rename it into place, so that a concurrent process
        # sees either no file or a complete one. Two processes that race both write identical
        # content, so whichever rename lands last is harmless.
        #
        # The temporary file is created with `open(..., "x")` rather than `tempfile`, because
        # `tempfile` creates owner-only (0600) files and the rename would preserve that mode,
        # making the cache unreadable to other users (e.g. a host user after a root Docker run).
        # `open` creates the file with the usual 0666 & ~umask permissions. The name uses a short
        # fixed prefix, not the cache name, so it stays within filesystem filename-length limits.
        tmp_path = path_to_call_graph.with_name(f".callgraph-{uuid.uuid4().hex}.tmp")
        try:
            with tmp_path.open("x", encoding="utf-8") as tmp:
                tmp.write(json.dumps(call_graph, indent=4))
            tmp_path.replace(path_to_call_graph)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise
    return str(path_to_call_graph)


def _is_cache_fresh(path_to_call_graph: Path, source_path: Path) -> bool:
    """Return True iff the cached call graph is at least as new as the source it was built from.

    Args:
        path_to_call_graph (Path): The cached call-graph file.
        source_path (Path): The C file, possibly a symlink.

    Returns:
        bool: True iff the cache is no older than the source file's contents (`stat`, which follows
            symlinks) and than the path itself (`lstat`, which changes when a symlink is repointed).
    """
    try:
        cache_mtime = path_to_call_graph.stat().st_mtime_ns
        source_mtime = max(source_path.stat().st_mtime_ns, source_path.lstat().st_mtime_ns)
    except OSError:
        return False
    return cache_mtime >= source_mtime


if __name__ == "__main__":
    main()
