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
from pathlib import Path

from tools.util import get_call_graph


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
    if path_to_call_graph.exists() and _is_cache_fresh(path_to_call_graph, source_path):
        return str(path_to_call_graph)
    call_graph = get_call_graph(path_to_file_to_verify)
    path_to_call_graph.write_text(json.dumps(call_graph, indent=4))
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
