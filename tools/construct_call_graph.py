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
        str: The path to the file to which the call graph is written.
    """
    call_graph = get_call_graph(path_to_file_to_verify)
    source_path = Path(path_to_file_to_verify)
    path_to_call_graph = source_path.with_name(f"{source_path.stem}-callgraph.json")
    path_to_call_graph.write_text(json.dumps(call_graph))
    return str(path_to_call_graph)
