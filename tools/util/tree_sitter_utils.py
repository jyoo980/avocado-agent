"""Utilities for working with tree-sitter ASTs."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

from pathlib import Path

import tree_sitter_c as tsc
from tree_sitter import Language, Node, Parser, Tree

from .callgraph import CallGraph
from .cbmc_clause_stripper import strip_cbmc_clauses

_TREE_SITTER_LANG = Language(tsc.language())
_PARSER = Parser(_TREE_SITTER_LANG)

# Names that tree-sitter parses as `call_expression` but are not real function calls in the
# CBMC sense: the `sizeof` operator. Contract macros (`__CPROVER_requires`, ...) are erased by
# `strip_cbmc_clauses` before parsing, so they never show up as call expressions in the AST.
_NON_CALLEE_NAMES = frozenset({"sizeof"})


def get_call_graph(path_to_file: str) -> CallGraph:
    """Return a call graph comprising functions parsed from the given file.

    Each caller's callees are split into `internal` (defined in the same file) and `external`
    (everything else — typically libc or other library calls). Downstream callers use this split
    to decide what to pass to CBMC's `--replace-call-with-contract` flag, which only makes sense
    for in-file callees.

    Args:
        path_to_file (str): The path to the file where the functions are defined.

    Returns:
        CallGraph: A call graph comprising functions from the given file.
    """
    file_content = Path(path_to_file).read_bytes()
    tree = _parse_to_ast(file_content)

    function_name_to_node: dict[str, Node] = dict(_iter_function_definitions(tree.root_node))
    in_file_functions = set(function_name_to_node)

    call_graph: dict[str, dict[str, list[str]]] = {}
    for function_name, node in function_name_to_node.items():
        callees = _get_names_of_functions_called_in_node(node)
        # This sorting isn't necessary for correctness, but makes call-graph construction
        # deterministic.
        internal_callees = sorted(name for name in callees if name in in_file_functions)
        external = sorted(name for name in callees if name not in in_file_functions)
        call_graph[function_name] = {"internal": internal_callees, "external": external}
    return CallGraph(call_graph)


def get_functions_with_cprover_annotations(path_to_file: str) -> set[str]:
    """Return names of functions in the given C file that carry at least one CBMC contract clause.

    A clause like `__CPROVER_requires(...)` is erased by `strip_cbmc_clauses` before parsing, so
    its byte range survives only in the returned span list. A function is "annotated" iff at
    least one such span lies between its `function_declarator` and its body.

    Args:
        path_to_file (str): The path to the C file to scan.

    Returns:
        set[str]: Names of functions in the file with at least one CBMC contract clause.
    """
    source = Path(path_to_file).read_bytes()
    stripped, spans = strip_cbmc_clauses(source)
    if not spans:
        return set()
    tree = _PARSER.parse(stripped)
    annotated: set[str] = set()
    for name, node in _iter_function_definitions(tree.root_node):
        declarator = get_function_declarator(node)
        body = node.child_by_field_name("body")
        if declarator is None or body is None:
            continue
        gap_start = declarator.end_byte
        gap_end = body.start_byte
        if any(gap_start <= span.start_byte < gap_end for span in spans):
            annotated.add(name)
    return annotated


def get_function_body(path_to_file: str, function_name: str) -> Node | None:
    """Return the function body (i.e., everything between the braces, exclusive) of a function.

    Args:
        path_to_file (str): The path to the file in which to look for the function.
        function_name (str): The name of the function whose body to return.

    Returns:
        Node | None: The body node of the function with the given name.
    """
    source = Path(path_to_file).read_bytes()
    tree = _parse_to_ast(source)
    target_function = get_function_definition(tree.root_node, function_name)
    if target_function is None:
        return None
    return target_function.child_by_field_name("body")


def get_function_definition(root: Node, name: str) -> Node | None:
    """DFS for the first `function_definition` node whose name matches `name`.

    Args:
        root (Node): The AST root to search under.
        name (str): The function name to match.

    Returns:
        Node | None: The matching function-definition node, or None if not found.
    """
    for fn_name, node in _iter_function_definitions(root):
        if fn_name == name:
            return node
    return None


def is_binary_operator_node(node: Node) -> bool:
    """Return True iff the given node is a binary operator node.

    Args:
        node (Node): The node to check for a binary operator.

    Returns:
        bool: True iff the given node is a binary operator node.
    """
    if node.type != "binary_expression":
        return False
    return node.child_by_field_name("operator") is not None


def get_function_declarator(fn_def: Node) -> Node | None:
    """Return the `function_declarator` node of a `function_definition`.

    For functions returning a pointer (e.g. `char *foo(...)`), tree-sitter wraps the
    `function_declarator` in one or more `pointer_declarator` nodes. Descend through any
    such wrappers to reach the underlying `function_declarator`.

    Args:
        fn_def (Node): The `function_definition` node.

    Returns:
        Node | None: The `function_declarator` child, or None if absent.
    """
    declarator = fn_def.child_by_field_name("declarator")
    while declarator is not None and declarator.type != "function_declarator":
        declarator = declarator.child_by_field_name("declarator")
    return declarator


def _parse_to_ast(content: bytes | str, language_extension: str = ".c") -> Tree:
    """Return a tree_sitter AST parsed from the given source code.

    CBMC contract clauses are erased (replaced with whitespace) before parsing so tree-sitter's
    C grammar produces a clean AST. Byte offsets, lines, and columns on the resulting tree
    continue to index the *original* source verbatim.

    This only supports parsing C ASTs, for now.

    Arguments:
        content (bytes | str): The source code to parse.
        language_extension (str): The language extension (including leading period) of the language
            to parse an AST for.  Defaults to ".c".

    Returns:
        Tree: A tree_sitter AST.

    """
    if language_extension != ".c":
        msg = f"Unsupported language for tree_sitter utils: {language_extension}"
        raise ValueError(msg)
    if isinstance(content, str):
        content = content.encode("utf-8")
    stripped, _ = strip_cbmc_clauses(content)
    return _PARSER.parse(stripped)


def dfs_traversal(root: Node) -> Iterator[Node]:
    """Return an DFS iterator over a tree_sitter node.

    Args:
        root (Node): The node from which to start a DFS traversal.

    Yields:
        Iterator[Node]: A DFS iterator over the given tree_sitter node.
    """
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(node.children)


def _get_function_definition_name(function_definition_node: Node) -> str:
    """Return the name of the function definition represented by the given node.

    Args:
        function_definition_node (Node): The function definition node.

    Returns:
        str: The name of the function definition represented by the given node.
    """
    declarator = function_definition_node.child_by_field_name("declarator")
    assert declarator, "A tree_sitter function_definition node must have a 'declarator' field"
    while declarator.type != "identifier":
        inner = declarator.child_by_field_name("declarator")
        assert inner, f"Unexpected declarator shape: {declarator.type}"
        declarator = inner
    assert declarator.text, "A tree_sitter identifier node must have a 'text' attribute"
    return declarator.text.decode("utf-8")


def _get_names_of_functions_called_in_node(node: Node) -> set[str]:
    """Return the names of functions that appear as call expressions in the given node.

    Args:
        node (Node): The node to search for function call expressions.

    Returns:
        set[str]: The names of functions that appear as call expressions in the given node.
    """
    names: set[str] = set()
    for descendant in dfs_traversal(node):
        if descendant is node:
            continue
        if descendant.type == "function_definition":
            # Skip nested function definitions; their calls belong to the inner function.
            continue
        if (
            descendant.type == "call_expression"
            and (function := descendant.child_by_field_name("function"))
            and function.type == "identifier"
        ):
            assert function.text, "A tree_sitter identifier node must have a 'text' attribute"
            name = function.text.decode("utf-8")
            if name in _NON_CALLEE_NAMES:
                continue
            names.add(name)
    return names


def _iter_function_definitions(root: Node) -> Iterator[tuple[str, Node]]:
    """Return an iterator yielding (name, definition_node) for every function defined under `root`.

    Args:
        root (Node): The root node.

    Yields:
        Iterator[tuple[str, Node]]: (name, definition_node) for every function defined under
            `root`.
    """
    for node in dfs_traversal(root):
        if node.type == "function_definition" and (name := _get_function_definition_name(node)):
            yield name, node
