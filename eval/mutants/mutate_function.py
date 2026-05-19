#!/usr/bin/env -S uv run --quiet python3

"""Mutant generator for C code.

We write our own instead of using off-the-shelf tools like Universal Mutator
(https://github.com/agroce/universalmutator), which is broken beyond Python 3.12, or other tools
because they are heavier-weight and may require integration with a compiler toolchain, which might
be too much upfront work, for now.

This module walks the body of an annotated function with tree-sitter, enumerates small mutations of
binary operators (e.g., arithmetic, relational, conditional, logical), and produces a mutant C
source string for each; leaving the function's contract clauses and every other function in the
file untouched.

Usage:

    % ./eval/mutants/mutate_function.py --function <NAME> --file <PATH_TO_C_FILE> [--out-dir <DIR>]

If `--out-dir` is given, each mutant is written to a separate `.c` file inside it. Otherwise a JSONL
summary is emitted to stdout.
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tree_sitter import Node


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.util.cbmc_clause_stripper import find_cbmc_annotation_spans  # noqa: E402
from tools.util.tree_sitter_utils import (  # noqa: E402
    dfs_traversal,
    get_function_body,
    is_binary_operator_node,
)


class MutationClasses(StrEnum):
    """Represent mutant classes."""

    RELATIONAL = "RELATIONAL"
    ARITHMETIC = "ARITHMETIC"
    LOGICAL = "LOGICAL"


_RELATIONAL_REPLACEMENTS: dict[str, list[str]] = {
    "<": ["<=", ">", ">=", "==", "!="],
    "<=": ["<", ">", ">=", "==", "!="],
    ">": ["<", "<=", ">=", "==", "!="],
    ">=": ["<", "<=", ">", "==", "!="],
    "==": ["!="],
    "!=": ["=="],
}

_ARITHMETIC_REPLACEMENTS: dict[str, list[str]] = {
    "+": ["-"],
    "-": ["+"],
    # Multiplication is omitted to avoid divide-by-zero errors/UB
    "/": ["*"],
    "%": ["*"],
}

_LOGICAL_REPLACEMENTS: dict[str, list[str]] = {
    "&&": ["||"],
    "||": ["&&"],
}

_OPERATOR_TO_REPLACEMENTS: dict[str, dict[str, list[str]]] = {
    MutationClasses.RELATIONAL: _RELATIONAL_REPLACEMENTS,
    MutationClasses.ARITHMETIC: _ARITHMETIC_REPLACEMENTS,
    MutationClasses.LOGICAL: _LOGICAL_REPLACEMENTS,
}

OPERATORS_TO_NAMES = {
    "+": "add",
    "-": "sub",
    "*": "mul",
    "/": "div",
    "%": "mod",
    "<": "lt",
    ">": "gt",
    "<=": "le",
    ">=": "ge",
    "==": "eq",
    "!=": "ne",
    "&&": "and",
    "||": "or",
}


@dataclass(frozen=True)
class Mutant:
    """A single body mutation derived from one operator replacement.

    `start_byte` and `end_byte` index into the *original* file's bytes. `mutant_source` is
    the full file text with that single operator replaced.
    """

    function: str
    operator_class: str
    original_operator: str
    replacement_operator: str
    start_byte: int
    end_byte: int
    line: int
    column: int
    mutant_source: str

    def get_unified_diff(self) -> str:
        """Return a unified diff between the original source and this mutant.

        Returns:
            str: A unified diff between the original source and this mutant.
        """
        mutant_bytes = self.mutant_source.encode("utf-8")
        repl_len = len(self.replacement_operator.encode("utf-8"))
        original_bytes = (
            mutant_bytes[: self.start_byte]
            + self.original_operator.encode("utf-8")
            + mutant_bytes[self.start_byte + repl_len :]
        )
        original_source = original_bytes.decode("utf-8")
        diff = difflib.unified_diff(
            original_source.splitlines(keepends=True),
            self.mutant_source.splitlines(keepends=True),
            fromfile="original",
            tofile="mutant",
            n=0,
        )
        return "".join(diff)


def main() -> None:
    """CLI entry point. generate mutants for one function; optionally write them to disk."""
    parser = argparse.ArgumentParser(
        description="Enumerate body-mutation candidates for a single annotated C function."
    )
    parser.add_argument("--function", required=True, help="Function whose body should be mutated.")
    parser.add_argument("--file", required=True, help="Path to the C file containing the function.")
    parser.add_argument(
        "--out-dir",
        default=None,
        help=(
            "When set, write each mutant to a stable filename under this directory and emit "
            "a JSONL manifest. Without it, only the in-memory mutant metadata is emitted."
        ),
    )
    args = parser.parse_args()

    mutants = get_mutants(args.file, args.function)
    if args.out_dir:
        paths = write_mutants_to_dir(mutants, Path(args.out_dir))
        for mutant, path in zip(mutants, paths, strict=True):
            record = {**asdict(mutant), "path": str(path)}
            # Drop the full source from the manifest line — the file already has it.
            record.pop("mutant_source", None)
            print(json.dumps(record))
    else:
        for mutant in mutants:
            record = asdict(mutant)
            # Just print the metadata, don't clog stdout with the file.
            record.pop("mutant_source", None)
            print(json.dumps(record))


def get_mutants(file_path: str, function_name: str) -> list[Mutant]:
    """Return mutants for the function with the given name in the file.

    Args:
        file_path (str): Path to the C source file.
        function_name (str): The function whose body should be mutated.

    Returns:
        list[Mutant]: One mutant per (binary operator site, replacement) pair.
    """
    source_code = Path(file_path).read_bytes()
    target_function_body = get_function_body(file_path, function_name)
    if not target_function_body:
        msg = f"Function '{function_name}' missing from file '{file_path}'"
        raise ValueError(msg)

    # In-body CBMC intrinsics (e.g. `__CPROVER_assume(__CPROVER_forall { ... i < 512 ... })`)
    # survive the top-level clause stripper, so tree-sitter still produces binary-expression nodes
    # for the operators they contain. Those operators belong to a contract assumption, not the
    # program under test, and mutating them yields meaningless mutants — filter them out here.
    body_start = target_function_body.start_byte
    body_end = target_function_body.end_byte
    in_body_annotation_spans = [
        s
        for s in find_cbmc_annotation_spans(source_code)
        if body_start <= s.start_byte and s.end_byte <= body_end
    ]

    mutants: list[Mutant] = []
    for op_class, table in _OPERATOR_TO_REPLACEMENTS.items():
        for node in dfs_traversal(target_function_body):
            if not is_binary_operator_node(node):
                continue
            op_node = node.child_by_field_name("operator")
            if not op_node:
                msg = f"Expected {node} to be a binary operator"
                raise ValueError(msg)
            if any(
                s.start_byte <= op_node.start_byte and op_node.end_byte <= s.end_byte
                for s in in_body_annotation_spans
            ):
                continue
            # Here, we've found a binary operator node in the target function body.
            original_operator = source_code[op_node.start_byte : op_node.end_byte].decode("utf-8")
            replacement_operators = table.get(original_operator, [])
            mutants.extend(
                [
                    _get_mutant(
                        source_code=source_code,
                        function_name=function_name,
                        mutation_class=op_class,
                        original_operator_node=op_node,
                        original_operator=original_operator,
                        replacement_operator=replacement_operator,
                    )
                    for replacement_operator in replacement_operators
                ]
            )
    return mutants


def _get_mutant(
    source_code: bytes,
    function_name: str,
    mutation_class: str,
    original_operator_node: Node,
    original_operator: str,
    replacement_operator: str,
) -> Mutant:
    """Return a mutant of the original source where the original operator node is replaced.

    Args:
        source_code (bytes): The original source code.
        function_name (str): The function to mutate.
        mutation_class (str): The class of mutation.
        original_operator_node (Node): The original operator node.
        original_operator (str): The original operator.
        replacement_operator (str): The replacement operator.

    Returns:
        Mutant: A mutant of the original source where the original operator node is replaced.
    """
    mutated_source_code = (
        source_code[: original_operator_node.start_byte]
        + replacement_operator.encode("utf-8")
        + source_code[original_operator_node.end_byte :]
    )
    return Mutant(
        function=function_name,
        operator_class=mutation_class,
        original_operator=original_operator,
        replacement_operator=replacement_operator,
        start_byte=original_operator_node.start_byte,
        end_byte=original_operator_node.end_byte,
        line=original_operator_node.start_point[0] + 1,
        column=original_operator_node.start_point[1],
        mutant_source=mutated_source_code.decode("utf-8"),
    )


def write_mutants_to_dir(mutants: list[Mutant], out_dir: Path) -> list[Path]:
    """Write each mutant's source to a file in `out_dir` and return the resulting paths.

    Filenames are stable: `<function>__<op_class>__<line>_<col>__<orig>_to_<repl>.c`. Each
    file is the full source of the original C file with one operator replaced — pass it
    straight to CBMC.

    Args:
        mutants (list[Mutant]): Mutants to materialize on disk.
        out_dir (Path): Destination directory; created if missing.

    Returns:
        list[Path]: Paths of the written mutant files, in the same order as `mutants`.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for m in mutants:
        safe_orig = OPERATORS_TO_NAMES.get(m.original_operator, "op")
        safe_repl = OPERATORS_TO_NAMES.get(m.replacement_operator, "op")
        name = (
            f"{m.function}__{m.operator_class}__{m.line}_{m.column}__{safe_orig}_to_{safe_repl}.c"
        )
        path = out_dir / name
        path.write_text(m.mutant_source, encoding="utf-8")
        paths.append(path)
    return paths


if __name__ == "__main__":
    sys.exit(main())
