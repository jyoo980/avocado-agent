"""Tool to obtain a topological ordering of functions from a call graph."""

import json
from pathlib import Path

from tools.avocado_tool_registry import mcp


@mcp.tool()
def get_topological_ordering_of_functions(
    path_to_call_graph: str,
) -> list[str]:
    """Return a topological ordering of functions from the given call graph JSON.

    Callees come before their callers, so verifying functions in this order means each
    function's in-file callees already have established contracts when CBMC reaches them. Only
    internal callees are considered; self-recursion and mutual recursion are tolerated (visited
    nodes are skipped, breaking back-edges in cycles).

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
