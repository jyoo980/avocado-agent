"""Utilities for working with tree-sitter ASTs."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

from pathlib import Path

import tree_sitter_c as tsc
from tree_sitter import Language, Node, Parser, Tree

_TREE_SITTER_LANG = Language(tsc.language())
_PARSER = Parser(_TREE_SITTER_LANG)

# Names that tree-sitter parses as `call_expression` but are not real function calls in the
# CBMC sense: contract macros (`__CPROVER_requires`, `__CPROVER_ensures`, ...) and the `sizeof`
# operator. They have no body to link, so excluding them keeps the call graph focused on real
# callees that affect verification.
_NON_CALLEE_PREFIXES = ("__CPROVER_",)
_NON_CALLEE_NAMES = frozenset({"sizeof"})


# [[MDE: I find the (undocumented) structure of the second dictionary a bit gross, because it is
# "stringly-typed".  I suggest instead defining a data structure named Calles or CGCallees or
# CallGraphCallees, which has two fields named "internal" and "external".  Then code like
# "cg.get("external", [])" can be replaced by "cg.external".]]
def get_call_graph(path_to_file: str) -> dict[str, dict[str, list[str]]]:
    """Return a call graph comprising functions parsed from the given file.

    Each caller's callees are split into `internal` (defined in the same file) and `external`
    (everything else — typically libc or other library calls). Downstream callers use this split
    to decide what to pass to CBMC's `--replace-call-with-contract` flag, which only makes sense
    for in-file callees.

    Args:
        path_to_file (str): The path to the file where the functions are defined.

    Returns:
        dict[str, dict[str, list[str]]]: Mapping from caller name to internal callees.
            [[MDE: That description is not correct, since it also contains external callees.]]
    """
    file_content = Path(path_to_file).read_text(encoding="utf-8")
    tree = _parse_to_ast(file_content)

    function_name_to_node: dict[str, Node] = {
        function_name: node
        for node in _dfs_traversal(tree.root_node)
        if node.type == "function_definition"
        and (function_name := _get_function_definition_name(node))
    }
    in_file_functions = set(function_name_to_node)

    call_graph: dict[str, dict[str, list[str]]] = {}
    for function_name, node in function_name_to_node.items():
        callees = _get_names_of_functions_called_in_node(node)
        # This sorting isn't necessary for correctness, but makes call-graph construction
        # deterministic.
        internal_callees = sorted(name for name in callees if name in in_file_functions)
        external = sorted(name for name in callees if name not in in_file_functions)
        call_graph[function_name] = {"internal": internal_callees, "external": external}
    return call_graph


def _parse_to_ast(content: str, language_extension: str = ".c") -> Tree:
    """Return a tree_sitter AST parsed from the given source code string.

    This only supports parsing C ASTs, for now.

    Arguments:
        content (str): The source code to parse.
        language_extension (str): The language extension (including leading period) of the language
            to parse an AST for.  Defaults to ".c".

    Returns:
        Tree: A tree_sitter AST.

    """
    match language_extension:
        case ".c":
            return _PARSER.parse(bytes(content, encoding="utf-8"))
        case _:
            msg = f"Unsupported language for tree_sitter utils: {language_extension}"
            raise ValueError(msg)


def _dfs_traversal(root: Node) -> Iterator[Node]:
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
    for descendant in _dfs_traversal(node):
        if (
            descendant.type == "call_expression"
            and (function := descendant.child_by_field_name("function"))
            and function.type == "identifier"
        ):
            assert function.text, "A tree_sitter identifier node must have a 'text' attribute"
            name = function.text.decode("utf-8")
            if name in _NON_CALLEE_NAMES or name.startswith(_NON_CALLEE_PREFIXES):
                continue
            names.add(name)
    return names
