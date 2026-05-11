#!/usr/bin/env -S uv run --quiet python3

"""Mutant generator for CBMC specifications.

For each `__CPROVER_*` contract clause in a specification attached to a function, generate a mutant
whose source is the original file with that single clause removed. All else remains untouched.

Usage:

    % ./eval/mutants/mutate_specification.py --function <NAME> \
            --file <PATH_TO_C_FILE> \
            [--out-dir <DIR>]

If `--out-dir` is given, each clause mutant is written to a separate `.c` file inside it.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import tree_sitter_c as tsc
from tree_sitter import Language, Node, Parser

from tools.util.tree_sitter_utils import get_function_declarator, get_function_definition

_TREE_SITTER_LANG = Language(tsc.language())
_PARSER = Parser(_TREE_SITTER_LANG)


class CbmcClause(StrEnum):
    """Represent a CBMC clause type."""

    REQUIRES = "__CPROVER_requires"
    ENSURES = "__CPROVER_ensures"
    ASSIGNS = "__CPROVER_assigns"
    FREES = "__CPROVER_frees"

    @staticmethod
    def is_clause(value: Any) -> bool:
        """Return True iff the value is a CBMC clause.

        Args:
            value (Any): The value to check for a CBMC clause.

        Returns:
            bool: True iff the value is a CBMC clause.
        """
        return any(value.startswith(clause.value) for clause in CbmcClause)


@dataclass(frozen=True)
class ClauseMutant:
    """Represent a mutated specification; comprises the original source with one clause removed.

    Attributes:
        function (str): The specified function.
        clause_kind (CbmcClause): The type of CBMC clause.
        clause_content (str): The clause itself.
        line (int): The line at which the clause was located.
        column (int): The column at which the clause began.
        mutant_source (str): The source code with the clause removed.
    """

    function: str
    clause_kind: CbmcClause
    clause_text: str
    line: int
    column: int
    mutant_source: str


def main() -> None:
    """CLI entry point: get clause-removal mutants and print a JSONL manifest."""
    parser = argparse.ArgumentParser(
        description="Enumerate clause-removal mutants for an annotated C function."
    )
    parser.add_argument("--function", required=True)
    parser.add_argument("--file", required=True)
    parser.add_argument(
        "--out-dir",
        default=None,
        help=(
            "When set, write each mutant to a stable filename under this directory and emit "
            "a JSONL manifest."
        ),
    )
    args = parser.parse_args()

    clause_mutants = get_clause_mutants(args.file, args.function)
    if out_dir := args.out_dir:
        paths = _write_clause_mutants_to_dir(clause_mutants, Path(out_dir))
        for clause_mutant, path in zip(clause_mutants, paths, strict=True):
            record = {**asdict(clause_mutant), "path": str(path)}
            record.pop("mutant_source", None)
            print(json.dumps(record))
    else:
        for mutant in get_clause_mutants(args.file, args.function):
            record = asdict(mutant)
            record.pop("mutant_source", None)
            print(json.dumps(record))


def get_clause_mutants(file_path: str, function_name: str) -> list[ClauseMutant]:
    """Return mutants for the CBMC clauses for the function with the given name in the file.

    Args:
        file_path (str): Path to the C source file.
        function_name (str): The function whose specification clauses should be mutated.

    Returns:
        list[ClauseMutant]: One mutant per removed clause.
    """
    source_code = Path(file_path).read_bytes()
    tree = _PARSER.parse(source_code)
    fn_def = get_function_definition(tree.root_node, function_name)
    if not fn_def:
        msg = f"Function '{function_name}' missing from file '{file_path}'"
        raise ValueError(msg)

    mutants: list[ClauseMutant] = []
    for clause in _get_cbmc_clause_nodes(fn_def):
        mutant_bytes = source_code[: clause.start_byte] + source_code[clause.end_byte :]
        mutants.append(
            ClauseMutant(
                function=function_name,
                clause_kind=_get_cbmc_clause_kind(clause),
                clause_text=source_code[clause.start_byte : clause.end_byte].decode("utf-8"),
                line=clause.start_point[0] + 1,
                column=clause.start_point[1],
                mutant_source=mutant_bytes.decode("utf-8"),
            )
        )
    return mutants


def _get_cbmc_clause_kind(node: Node) -> CbmcClause:
    """Return the CbmcClause for the given node.

    Args:
        node (Node): The node for which to return a CbmcClause.

    Returns:
        CbmcClause: The CbmcClause for the given node.
    """
    if not node.text:
        msg = f"Expected a candidate CbmcClause node to have a .text attribute, but got: {node}"
        raise ValueError(msg)
    node_text = node.text.decode("utf-8").strip()
    for clause_kind in CbmcClause:
        if node_text.startswith(clause_kind.value):
            return clause_kind
    msg = f"Unable to find a CbmcClause kind for {node}"
    raise ValueError(msg)


def _get_cbmc_clause_nodes(fn_def: Node) -> list[Node]:
    """Return the contract-clause `call_expression` nodes attached to a function definition.

    Args:
        fn_def (Node): The `function_definition` AST node.

    Returns:
        list[Node]: Clause nodes in source order.
    """
    declarator = get_function_declarator(fn_def)
    clause_candidates: list[Node] = []
    if declarator is not None:
        clause_candidates.extend(declarator.children)

    for child in fn_def.children:
        if child.type == "ERROR":
            # tree-sitter ERROR nodes often represent clauses, since `__CPROVER_` syntax is not
            # legal C that parses cleanly.
            clause_candidates.extend(child.children)
    return [node for node in clause_candidates if _is_cbmc_clause_node(node)]


def _is_cbmc_clause_node(node: Node) -> bool:
    """Return True iff the node represents a CBMC specification clause.

    Args:
        node (Node): The node to check for a CBMC specification clause.

    Returns:
        bool: True iff the node represents a CBMC specification clause.
    """
    if node.type != "call_expression":
        return False
    fn = node.child_by_field_name("function")
    if not fn or fn.type != "identifier" or not fn.text:
        return False
    return CbmcClause.is_clause(fn.text.decode("utf-8"))


def _write_clause_mutants_to_dir(
    clause_mutants: list[ClauseMutant], path_to_write: Path
) -> list[Path]:
    """Write each clause mutant's source to a file in `path_to_write` and return the paths.

    Filenames are stable: `<function>__<clause_kind>__<line>_<col>.c`. Each file is the
    full source of the original C file with one clause removed.

    Args:
        clause_mutants (list[ClauseMutant]): Mutants to materialize on disk.
        path_to_write (Path): Destination directory; created if missing.

    Returns:
        list[Path]: Paths of the written mutant files, in the same order as `clause_mutants`.
    """
    path_to_write.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for clause_mutant in clause_mutants:
        name = (
            f"{clause_mutant.function}__{clause_mutant.clause_kind.name}"
            f"__{clause_mutant.line}_{clause_mutant.column}.c"
        )
        path = path_to_write / name
        path.write_text(clause_mutant.mutant_source, encoding="utf-8")
        paths.append(path)
    return paths


if __name__ == "__main__":
    sys.exit(main())
