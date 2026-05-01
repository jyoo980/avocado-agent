"""Tool to construct a call graph comprising functions in a given file."""

import json
from pathlib import Path

from tools.avocado_tool_registry import mcp
from tools.util import get_call_graph


@mcp.tool()
def construct_call_graph(
    path_to_file_to_verify: str,
) -> str:
    """Construct a call graph comprised of functions parsed from the given C file.

    Args:
        path_to_file_to_verify (str): The path to the file from which to construct a call graph.

    Returns:
        str: The path to the file to which the call graph is written, as a JSON dictionary.
    """
    call_graph = get_call_graph(path_to_file_to_verify)
    path_to_call_graph = f"{Path(path_to_file_to_verify).stem}-callgraph.json"
    Path(path_to_call_graph).write_text(json.dumps(call_graph))
    return path_to_call_graph
