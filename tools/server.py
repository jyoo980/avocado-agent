"""Stdio MCP server exposing all CBMC tools to Claude Code."""

# Imported for the `@mcp.tool()` registration side effects.
from tools.avocado_tool_registry import mcp
from tools.construct_call_graph import construct_call_graph  # noqa: F401
from tools.get_topological_ordering_of_functions import (  # noqa: F401
    get_topological_ordering_of_functions,
)
from tools.run_cbmc import run_cbmc  # noqa: F401

if __name__ == "__main__":
    mcp.run(transport="stdio")
