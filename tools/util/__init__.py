"""Utilities to be used by tools.

Note: `tools.util.mutation` is intentionally not re-exported here. It depends on
`tools.run_cbmc`, which in turn depends on `tools.util`, so importing it eagerly
from this package's `__init__` would create a circular import at package load
time. Consumers should `from tools.util.mutation import ...` directly.
"""

from .callgraph import CallGraph, CallGraphCallees
from .include_dirs import autodetect_include_dirs, get_include_dirs
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
    "get_include_dirs",
    "get_stub_paths_for",
    "get_unstubbed_external_callees_for",
    "run_command",
]
