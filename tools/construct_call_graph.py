"""Saves a call graph to a file and prints the file name.

This script expects a single C source file as input, and computes a call graph based on the
functions that are immediately available in the file. For example, if a function `a` calls `b` and
`c`, where `b` is defined in the same file but `c` is not (i.e., it is defined elsewhere), the call
graph will map `b` to `a`'s list of internal callees and `c` to a list of external callees.
See utils/callgraph.py for more information about the structure of the call graph.

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
    if path_to_call_graph.exists():
        return str(path_to_call_graph)
    call_graph = get_call_graph(path_to_file_to_verify)
    path_to_call_graph.write_text(json.dumps(call_graph, indent=4))
    return str(path_to_call_graph)


if __name__ == "__main__":
    main()
