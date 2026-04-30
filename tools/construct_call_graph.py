"""Stdio MCP server exposing a `construct_call_graph` tool to Claude Code."""

import json
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .util import get_call_graph

mcp = FastMCP(name="construct_call_graph")


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


if __name__ == "__main__":
    mcp.run(transport="stdio")
