"""Helpers for extracting function signatures and contract clauses from C source.

Shared by the spec-comparison and harness-codegen tools. Centralized here so the
extraction logic doesn't drift between modules. Tree-sitter is the parser of record.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import tree_sitter_c as tsc
from tree_sitter import Language, Node, Parser

if TYPE_CHECKING:
    from collections.abc import Iterator

_TREE_SITTER_LANG = Language(tsc.language())
_PARSER = Parser(_TREE_SITTER_LANG)

_CONTRACT_CLAUSE_NAMES = frozenset(
    {
        "__CPROVER_requires",
        "__CPROVER_ensures",
        "__CPROVER_assigns",
        "__CPROVER_frees",
    }
)


@dataclass(frozen=True)
class ParameterInfo:
    """A single function parameter, captured for harness codegen."""

    name: str
    type_text: str
    declarator_text: str  # e.g. "int *p" or "int arr[]" — full token sequence


@dataclass(frozen=True)
class FunctionSpec:
    """Everything the comparison harness needs about one annotated function."""

    name: str
    return_type_text: str
    parameters: list[ParameterInfo]
    requires: list[str] = field(default_factory=list)
    ensures: list[str] = field(default_factory=list)
    assigns: list[str] = field(default_factory=list)
    frees: list[str] = field(default_factory=list)


def extract_function_spec(file_path: str, function_name: str) -> FunctionSpec | None:
    """Return the `FunctionSpec` for `function_name` in `file_path`, or None if absent.

    Args:
        file_path (str): Path to the C source file.
        function_name (str): The function whose signature and clauses to extract.

    Returns:
        FunctionSpec | None: The extracted spec, or None when the function is missing.
    """
    content = Path(file_path).read_bytes()
    tree = _PARSER.parse(content)
    fn_def = _find_function_definition(tree.root_node, function_name)
    if fn_def is None:
        return None

    return_type_node = fn_def.child_by_field_name("type")
    return_type_text = (
        content[return_type_node.start_byte : return_type_node.end_byte].decode("utf-8").strip()
        if return_type_node is not None
        else ""
    )
    params = _extract_parameters(fn_def, content)
    requires: list[str] = []
    ensures: list[str] = []
    assigns: list[str] = []
    frees: list[str] = []
    for clause in _extract_clauses(fn_def):
        kind = _clause_kind(clause)
        arg_text = _clause_arg_text(clause, content)
        if arg_text is None:
            continue
        {"requires": requires, "ensures": ensures, "assigns": assigns, "frees": frees}[kind].append(
            arg_text
        )
    return FunctionSpec(
        name=function_name,
        return_type_text=return_type_text,
        parameters=params,
        requires=requires,
        ensures=ensures,
        assigns=assigns,
        frees=frees,
    )


def _extract_parameters(fn_def: Node, content: bytes) -> list[ParameterInfo]:
    declarator = _find_function_declarator(fn_def)
    if declarator is None:
        return []
    parameter_list = next((c for c in declarator.children if c.type == "parameter_list"), None)
    if parameter_list is None:
        return []
    out: list[ParameterInfo] = []
    for child in parameter_list.children:
        if child.type != "parameter_declaration":
            continue
        type_node = child.child_by_field_name("type")
        param_decl = child.child_by_field_name("declarator")
        if type_node is None or param_decl is None:
            continue
        name = _resolve_param_name(param_decl)
        if name is None:
            continue
        type_text = content[type_node.start_byte : type_node.end_byte].decode("utf-8").strip()
        declarator_text = content[child.start_byte : child.end_byte].decode("utf-8").strip()
        out.append(ParameterInfo(name=name, type_text=type_text, declarator_text=declarator_text))
    return out


def _resolve_param_name(declarator: Node) -> str | None:
    while True:
        if declarator.type in {"pointer_declarator", "array_declarator"}:
            inner = declarator.child_by_field_name("declarator")
            if inner is None:
                return None
            declarator = inner
        elif declarator.type == "identifier":
            return declarator.text.decode("utf-8") if declarator.text else None
        elif declarator.type == "parenthesized_declarator":
            inner = next(
                (c for c in declarator.children if c.type not in {"(", ")"}),
                None,
            )
            if inner is None:
                return None
            declarator = inner
        else:
            inner = declarator.child_by_field_name("declarator")
            if inner is None:
                return None
            declarator = inner


def _extract_clauses(fn_def: Node) -> list[Node]:
    declarator = _find_function_declarator(fn_def)
    candidates: list[Node] = []
    if declarator is not None:
        candidates.extend(declarator.children)
    for child in fn_def.children:
        if child.type == "ERROR":
            candidates.extend(child.children)
    clauses: list[Node] = []
    for node in candidates:
        if node.type != "call_expression":
            continue
        fn = node.child_by_field_name("function")
        if fn is None or fn.type != "identifier" or not fn.text:
            continue
        if fn.text.decode("utf-8") in _CONTRACT_CLAUSE_NAMES:
            clauses.append(node)
    return clauses


def _clause_kind(clause: Node) -> str:
    fn = clause.child_by_field_name("function")
    assert fn is not None and fn.text is not None
    return fn.text.decode("utf-8").removeprefix("__CPROVER_")


def _clause_arg_text(clause: Node, content: bytes) -> str | None:
    """Return the full argument-list text of a clause, stripped of its surrounding parens.

    For `__CPROVER_requires(x > 0 && y > 0)` returns `"x > 0 && y > 0"`. For
    `__CPROVER_assigns(*a, *b)` returns `"*a, *b"`. Whitespace is preserved as-is so the
    result drops verbatim into a harness.

    Args:
        clause (Node): The contract clause `call_expression` node.
        content (bytes): The full file content, used to slice the clause's text.

    Returns:
        str | None: The interior of the argument list, or None when the node has no args.
    """
    args = clause.child_by_field_name("arguments")
    if args is None:
        return None
    inner = content[args.start_byte + 1 : args.end_byte - 1].decode("utf-8")
    return inner.strip() or None


def _find_function_definition(root: Node, name: str) -> Node | None:
    for n in _walk(root):
        if n.type != "function_definition":
            continue
        declarator = _find_function_declarator(n)
        if declarator is None:
            continue
        ident = declarator
        while ident.type != "identifier":
            inner = ident.child_by_field_name("declarator")
            if inner is None:
                break
            ident = inner
        if ident.type == "identifier" and ident.text and ident.text.decode("utf-8") == name:
            return n
    return None


def _find_function_declarator(fn_def: Node) -> Node | None:
    declared = fn_def.child_by_field_name("declarator")
    if declared is not None and declared.type == "function_declarator":
        return declared
    for descendant in _walk(fn_def):
        if descendant.type == "function_declarator":
            return descendant
    return declared


def _walk(node: Node) -> Iterator[Node]:
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(n.children)
