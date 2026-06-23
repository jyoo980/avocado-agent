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
from typing import TYPE_CHECKING

import tree_sitter_c as tsc
from tree_sitter import Language, Parser

from tools.util.cbmc_clause_stripper import (
    CbmcClauseSpan,
    strip_all_cbmc_annotations,
    strip_cbmc_clauses,
)
from tools.util.tree_sitter_utils import get_function_declarator, get_function_definition

if TYPE_CHECKING:
    from tree_sitter import Node

_TREE_SITTER_LANG = Language(tsc.language())
_PARSER = Parser(_TREE_SITTER_LANG)


class CbmcClause(StrEnum):
    """Represent a CBMC clause type."""

    REQUIRES = "__CPROVER_requires"
    ENSURES = "__CPROVER_ensures"
    ASSIGNS = "__CPROVER_assigns"
    FREES = "__CPROVER_frees"


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
        for mutant in clause_mutants:
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
    _, spans = strip_cbmc_clauses(source_code)
    # Parse the fully-stripped buffer so in-body loop contracts don't corrupt the tree; the
    # contract-clause `spans` are still what gets mutated below.
    tree = _PARSER.parse(strip_all_cbmc_annotations(source_code))
    fn_def = get_function_definition(tree.root_node, function_name)
    if not fn_def:
        msg = f"Function '{function_name}' missing from file '{file_path}'"
        raise ValueError(msg)

    mutants: list[ClauseMutant] = []
    for span in _get_clause_spans_for_function(fn_def, spans):
        mutant_bytes = source_code[: span.start_byte] + source_code[span.end_byte :]
        line, column = _byte_offset_to_line_column(source_code, span.start_byte)
        mutants.append(
            ClauseMutant(
                function=function_name,
                clause_kind=CbmcClause(span.kind),
                clause_text=source_code[span.start_byte : span.end_byte].decode("utf-8"),
                line=line,
                column=column,
                mutant_source=mutant_bytes.decode("utf-8"),
            )
        )
    return mutants


def _get_clause_spans_for_function(
    fn_def: Node, spans: list[CbmcClauseSpan]
) -> list[CbmcClauseSpan]:
    """Return clause spans that decorate the given function definition, in source order.

    A clause "decorates" `fn_def` when it lies between the end of its `function_declarator` and
    the start of its body (`compound_statement`). Spans that fall outside this gap belong to a
    different function in the file.

    Args:
        fn_def (Node): The `function_definition` node parsed from the clause-stripped source.
        spans (list[CbmcClauseSpan]): All clause spans in the file, in source order.

    Returns:
        list[CbmcClauseSpan]: The subset of `spans` attached to `fn_def`.
    """
    declarator = get_function_declarator(fn_def)
    body = fn_def.child_by_field_name("body")
    if declarator is None or body is None:
        return []
    gap_start = declarator.end_byte
    gap_end = body.start_byte
    return [span for span in spans if gap_start <= span.start_byte < gap_end]


def _byte_offset_to_line_column(source: bytes, offset: int) -> tuple[int, int]:
    """Return the 1-based line and 0-based column for a byte offset into `source`.

    Args:
        source (bytes): The source bytes the offset refers to.
        offset (int): The byte offset.

    Returns:
        tuple[int, int]: (line, column) where line is 1-based and column is 0-based, matching
            tree-sitter's `start_point` convention.
    """
    prefix = source[:offset]
    line = prefix.count(b"\n") + 1
    last_newline = prefix.rfind(b"\n")
    column = offset - (last_newline + 1) if last_newline >= 0 else offset
    return line, column


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
