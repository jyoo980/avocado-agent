"""Utilities to be used by tools."""

from .callgraph import CallGraph, CallGraphCallees
from .paths import autodetect_include_dirs, resolve_parent
from .stubs import (
    build_stub_index,
    get_in_file_callees_for,
    get_in_file_callers_of,
    get_stub_paths_for,
    get_unstubbed_external_callees_for,
)
from .subprocesses import run_command
from .tree_sitter_utils import (
    FunctionSloc,
    get_call_graph,
    get_function_definition,
    get_function_sloc,
    get_functions_with_cprover_annotations,
)

__all__ = [
    "CallGraph",
    "CallGraphCallees",
    "FunctionSloc",
    "autodetect_include_dirs",
    "build_stub_index",
    "get_call_graph",
    "get_function_definition",
    "get_function_sloc",
    "get_functions_with_cprover_annotations",
    "get_in_file_callees_for",
    "get_in_file_callers_of",
    "get_stub_paths_for",
    "get_unstubbed_external_callees_for",
    "resolve_parent",
    "run_command",
]
