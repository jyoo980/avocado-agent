"""CLI to construct a call graph comprising functions in a given file."""

import argparse
import json
from pathlib import Path

from tools.util import get_call_graph


def construct_call_graph(
    path_to_file_to_verify: str,
) -> str:
    """Construct a call graph comprised of functions parsed from the given C file.

    Args:
        path_to_file_to_verify (str): The path to the file from which to construct a call graph.

    Returns:
        str: The path to the file to which the call graph is written.
    """
    call_graph = get_call_graph(path_to_file_to_verify)
    source_path = Path(path_to_file_to_verify)
    path_to_call_graph = source_path.with_name(f"{source_path.stem}-callgraph.json")
    path_to_call_graph.write_text(json.dumps(call_graph, indent=4))
    return str(path_to_call_graph)


def main() -> None:
    """CLI to construct a call graph comprising functions in a given file."""
    parser = argparse.ArgumentParser(
        description="Parse a C file and write a call graph JSON next to it."
    )
    parser.add_argument(
        "path_to_file_to_verify",
        help="Path to the C file from which to construct a call graph.",
    )
    args = parser.parse_args()
    print(construct_call_graph(args.path_to_file_to_verify))


if __name__ == "__main__":
    main()
