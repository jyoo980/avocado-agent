"""Print a reverse topological ordering of functions in a file to stdout.

Usage:
    % avocado-topological-order <PATH_TO_SOURCE_FILE>
"""

import argparse
import json
from pathlib import Path

from tools import construct_call_graph


def main() -> None:
    """Print a reverse topological ordering of functions from a file."""
    parser = argparse.ArgumentParser(
        description=(
            "Print a reverse topological ordering (i.e., callee-first) of functions from a file, "
            "one function name per line."
        )
    )
    parser.add_argument(
        "path_to_source_file",
        help=(
            "Path to the source file containing the functions to print in reverse topological order"
        ),
    )
    args = parser.parse_args()
    call_graph = construct_call_graph(args.path_to_call_graph)
    for function in get_topological_ordering_of_functions(call_graph):
        print(function)


def get_topological_ordering_of_functions(
    path_to_call_graph: str,
) -> list[str]:
    """Return a topological ordering of functions from the given call graph JSON.

    Callees come before their callers, so verifying functions in this order means each function's
    in-file callees already have established contracts when CBMC reaches them. In case of mutual
    recursion, functions in the SCC are printed in arbitary order.

    Args:
        path_to_call_graph (str): The path to the call graph JSON.

    Returns:
        list[str]: Function names ordered callees-first.

    """
    call_graph: dict[str, dict[str, list[str]]] = json.loads(Path(path_to_call_graph).read_text())

    visited: set[str] = set()
    ordering: list[str] = []

    def visit(function: str) -> None:
        if function in visited or function not in call_graph:
            return
        visited.add(function)
        for callee in call_graph[function].get("internal", []):
            visit(callee)
        ordering.append(function)

    for function in call_graph:
        visit(function)
    return ordering


if __name__ == "__main__":
    main()
