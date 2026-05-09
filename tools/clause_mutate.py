"""Spec-clause mutation engine for CBMC clause-removal redundancy (M2.3).

For each `__CPROVER_*` contract clause attached to a function, produce a mutant whose source
is the original file with that single clause removed. The body is left untouched. The
CBMC-driven scoring loop in `eval/clause_redundancy.py` decides which clauses are
redundant for soundness (verification still succeeds without them).

Usage (pure enumeration):
    avocado-mutate-clauses --function <NAME> --file <PATH_TO_C_FILE>
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
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
class ClauseMutant:
    """A single spec mutant: the original source with one contract clause removed."""

    function: str
    clause_kind: str  # "requires" | "ensures" | "assigns" | "frees"
    clause_text: str  # the original clause source (e.g. "__CPROVER_requires(...)" )
    line: int
    column: int
    mutant_source: str


def enumerate_clause_mutants(file_path: str, function_name: str) -> list[ClauseMutant]:
    """Return one mutant per contract clause attached to `function_name`.

    The clause's byte range is removed verbatim. Surrounding whitespace is left in place;
    it does not affect compilation.

    Args:
        file_path (str): Path to the C source file.
        function_name (str): The function whose contract clauses should be mutated.

    Returns:
        list[ClauseMutant]: One mutant per contract clause; empty when the function or its
            contract clauses cannot be found.
    """
    content = Path(file_path).read_bytes()
    tree = _PARSER.parse(content)
    fn_def = _find_function_definition(tree.root_node, function_name)
    if fn_def is None:
        return []
    clauses = _extract_clauses(fn_def)
    mutants: list[ClauseMutant] = []
    for clause in clauses:
        mutant_bytes = content[: clause.start_byte] + content[clause.end_byte :]
        mutants.append(
            ClauseMutant(
                function=function_name,
                clause_kind=_clause_kind(clause),
                clause_text=content[clause.start_byte : clause.end_byte].decode("utf-8"),
                line=clause.start_point[0] + 1,
                column=clause.start_point[1],
                mutant_source=mutant_bytes.decode("utf-8"),
            )
        )
    return mutants


def _extract_clauses(fn_def: Node) -> list[Node]:
    """Return the contract-clause `call_expression` nodes attached to a function definition.

    Args:
        fn_def (Node): The `function_definition` AST node.

    Returns:
        list[Node]: Clause nodes in source order.
    """
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


def main() -> int:
    """CLI entry point: enumerate clause-removal mutants and print a JSONL manifest.

    Returns:
        int: 0 on success.
    """
    parser = argparse.ArgumentParser(
        description="Enumerate clause-removal mutants for an annotated C function."
    )
    parser.add_argument("--function", required=True)
    parser.add_argument("--file", required=True)
    args = parser.parse_args()
    for mutant in enumerate_clause_mutants(args.file, args.function):
        record = asdict(mutant)
        record.pop("mutant_source", None)
        print(json.dumps(record))
    return 0


if __name__ == "__main__":
    sys.exit(main())
