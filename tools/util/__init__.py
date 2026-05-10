"""Utilities to be used by tools."""

from .callgraph import CallGraph, CallGraphCallees
from .stubs import (
    build_stub_index,
    get_in_file_callees_for,
    get_stub_paths_for,
    get_unstubbed_external_callees_for,
)
from .tree_sitter_utils import (
    get_call_graph,
    get_function_definition,
    get_functions_with_cprover_annotations,
)

__all__ = [
    "CallGraph",
    "CallGraphCallees",
    "build_stub_index",
    "get_call_graph",
    "get_function_definition",
    "get_functions_with_cprover_annotations",
    "get_in_file_callees_for",
    "get_stub_paths_for",
    "get_unstubbed_external_callees_for",
]
