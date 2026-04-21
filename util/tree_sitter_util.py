from __future__ import annotations

from pathlib import Path

import tree_sitter_c
from tree_sitter import Language, Node, Parser

from util.c_function import CFunction

C_LANGUAGE = Language(tree_sitter_c.language())


def _make_parser() -> Parser:
    parser = Parser(C_LANGUAGE)
    return parser


def parse_file(file_path: Path) -> list[CFunction]:
    """Extract all top-level function definitions from a C source file."""
    source = file_path.read_bytes()
    parser = _make_parser()
    tree = parser.parse(source)
    return _extract_functions(tree.root_node, file_path)


def _extract_functions(root: Node, file_path: Path) -> list[CFunction]:
    functions: list[CFunction] = []
    for node in root.children:
        if node.type == "function_definition":
            name = _get_function_name(node)
            if name:
                start_line = node.start_point[0] + 1  # tree-sitter is 0-indexed
                end_line = node.end_point[0] + 1
                functions.append(CFunction(name, file_path, start_line, end_line))
    return functions


def _get_function_name(node: Node) -> str | None:
    declarator = node.child_by_field_name("declarator")
    if declarator is None:
        return None
    return _extract_name_from_declarator(declarator)


def _extract_name_from_declarator(node: Node) -> str | None:
    if node.type == "function_declarator":
        inner = node.child_by_field_name("declarator")
        if inner is not None:
            return _extract_name_from_declarator(inner)
    if node.type == "identifier":
        return node.text.decode("utf-8")
    if node.type in ("pointer_declarator",):
        inner = node.child_by_field_name("declarator")
        if inner is not None:
            return _extract_name_from_declarator(inner)
    return None


def get_call_sites(file_path: Path, function_name: str) -> list[str]:
    """Return the names of all functions called within the named function."""
    source = file_path.read_bytes()
    parser = _make_parser()
    tree = parser.parse(source)

    target_node = _find_function_node(tree.root_node, function_name)
    if target_node is None:
        return []

    callees: list[str] = []
    _collect_calls(target_node, callees)
    return list(dict.fromkeys(callees))  # deduplicate, preserve order


def _find_function_node(root: Node, name: str) -> Node | None:
    for node in root.children:
        if node.type == "function_definition":
            n = _get_function_name(node)
            if n == name:
                return node
    return None


def _collect_calls(node: Node, out: list[str]) -> None:
    if node.type == "call_expression":
        fn_node = node.child_by_field_name("function")
        if fn_node is not None and fn_node.type == "identifier":
            out.append(fn_node.text.decode("utf-8"))
    for child in node.children:
        _collect_calls(child, out)
