"""Mutation engine for CBMC body-mutation kill rate (M2.1).

This module is the pure, CBMC-free side of body-mutation testing: it walks the body of an
annotated function with tree-sitter, enumerates small syntactic mutations of binary
operators (Arithmetic, Relational, Conditional, Logical), and produces a mutant C source
string for each — leaving the function's contract clauses and every other function in the
file untouched.

The CBMC-driven scoring loop lives in `eval/mutation_score.py`, which feeds these
mutants to `tools.run_cbmc.run_cbmc` and computes the kill rate.

Usage (pure enumeration, no CBMC):
    avocado-mutate-function --function <NAME> --file <PATH_TO_C_FILE> [--out-dir <DIR>]

If `--out-dir` is given, each mutant is written to a separate `.c` file inside it and a
manifest is printed; otherwise a JSONL summary is emitted to stdout.
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

# Mutation operator tables. Each maps an original operator text to the mutant operator
# texts it should be replaced with. Keep replacements that produce *syntactically valid*
# C of the same arity.
_RELATIONAL: dict[str, list[str]] = {
    "<": ["<=", ">", "==", "!="],
    "<=": ["<", ">=", "==", "!="],
    ">": [">=", "<", "==", "!="],
    ">=": [">", "<=", "==", "!="],
    "==": ["!="],
    "!=": ["=="],
}

_ARITHMETIC: dict[str, list[str]] = {
    "+": ["-"],
    "-": ["+"],
    "*": ["/"],
    "/": ["*"],
    "%": ["*"],
}

_CONDITIONAL: dict[str, list[str]] = {
    "&&": ["||"],
    "||": ["&&"],
}

_OPERATOR_TABLES: dict[str, dict[str, list[str]]] = {
    "ROR": _RELATIONAL,
    "AOR": _ARITHMETIC,
    "COR": _CONDITIONAL,
}


@dataclass(frozen=True)
class Mutant:
    """A single body mutation derived from one operator replacement.

    `start_byte` and `end_byte` index into the *original* file's bytes. `mutant_source` is
    the full file text with that single operator replaced.
    """

    function: str
    operator_class: str
    original: str
    replacement: str
    start_byte: int
    end_byte: int
    line: int
    column: int
    mutant_source: str


def enumerate_mutants(file_path: str, function_name: str) -> list[Mutant]:
    """Return every body mutant for `function_name` in `file_path`.

    Walks the function body's `binary_expression` nodes; for each operator in our mutation
    tables, generates one mutant per replacement candidate. The contract clauses sit
    outside the body and are never touched.

    Args:
        file_path (str): Path to the C source file.
        function_name (str): The function whose body should be mutated.

    Returns:
        list[Mutant]: One mutant per (binary operator site, replacement) pair.
    """
    content = Path(file_path).read_bytes()
    tree = _PARSER.parse(content)
    fn_def = _find_function_definition(tree.root_node, function_name)
    if fn_def is None:
        return []
    body = fn_def.child_by_field_name("body")
    if body is None:
        return []

    mutants: list[Mutant] = []
    for op_class, table in _OPERATOR_TABLES.items():
        for node in _walk(body):
            if node.type != "binary_expression":
                continue
            op_node = node.child_by_field_name("operator")
            if op_node is None:
                continue
            op_text = content[op_node.start_byte : op_node.end_byte].decode("utf-8")
            replacements = table.get(op_text, [])
            for replacement in replacements:
                mutated = (
                    content[: op_node.start_byte]
                    + replacement.encode("utf-8")
                    + content[op_node.end_byte :]
                )
                mutants.append(
                    Mutant(
                        function=function_name,
                        operator_class=op_class,
                        original=op_text,
                        replacement=replacement,
                        start_byte=op_node.start_byte,
                        end_byte=op_node.end_byte,
                        line=op_node.start_point[0] + 1,
                        column=op_node.start_point[1],
                        mutant_source=mutated.decode("utf-8"),
                    )
                )
    return mutants


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
        safe_orig = _safe_filename(m.original)
        safe_repl = _safe_filename(m.replacement)
        name = (
            f"{m.function}__{m.operator_class}__{m.line}_{m.column}__{safe_orig}_to_{safe_repl}.c"
        )
        path = out_dir / name
        path.write_text(m.mutant_source, encoding="utf-8")
        paths.append(path)
    return paths


def _safe_filename(text: str) -> str:
    """Map operator text to a filename-safe token.

    Args:
        text (str): The original operator text (e.g. `<=`).

    Returns:
        str: A short identifier safe for use in filenames.
    """
    table = {
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
    return table.get(text, "op")


def _find_function_definition(root: Node, name: str) -> Node | None:
    """DFS for the first `function_definition` whose name matches `name`.

    Args:
        root (Node): The AST root to search under.
        name (str): The function name to match.

    Returns:
        Node | None: The matching `function_definition` node, or None if not found.
    """
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
    """Locate the `function_declarator` even when wrapped in `array_declarator`/`ERROR`.

    Mirrors the recovery in `tools/static_metrics.py` because contract clauses with subscript
    expressions can confuse tree-sitter's grammar.

    Args:
        fn_def (Node): The `function_definition` node.

    Returns:
        Node | None: The recovered `function_declarator`, or the original declarator child
            (which may not be a `function_declarator`) when no recovery is possible.
    """
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
    """CLI entry point. Enumerate mutants for one function; optionally write them to disk.

    Returns:
        int: 0 on success.
    """
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

    mutants = enumerate_mutants(args.file, args.function)
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
            record.pop("mutant_source", None)
            print(json.dumps(record))
    return 0


if __name__ == "__main__":
    sys.exit(main())
