"""Saves a call graph to a file and prints the file name.

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

    Args:
        path_to_file_to_verify (str): The path to the file from which to construct a call graph.

    Returns:
        str: The path to the file to which the call graph is written.
    """
    source_path = Path(path_to_file_to_verify)
    path_to_call_graph = source_path.with_name(f"{source_path.stem}-callgraph.json")
    if _call_graph_exists(source_path, path_to_call_graph):
        return str(path_to_call_graph)
    call_graph = get_call_graph(path_to_file_to_verify)
    path_to_call_graph.write_text(json.dumps(call_graph, indent=4))
    return str(path_to_call_graph)


def _call_graph_exists(path_to_file_to_verify: Path, expected_call_graph_path: Path) -> bool:
    """Return True if the call graph exists.

    Note: This function checks if the call graph exists and if it is newer than the source file
    from which it is constructed.

    Args:
        path_to_file_to_verify (Path): Path to the source file to verify.
        expected_call_graph_path (Path): The expected path to the call graph.

    Returns:
        bool: True iff the call graph exists and if it is newer than the source file from which it
            is constructed.
    """
    return (
        expected_call_graph_path.exists()
        and path_to_file_to_verify.stat().st_mtime >= path_to_file_to_verify.stat().st_mtime
    )


if __name__ == "__main__":
    main()
