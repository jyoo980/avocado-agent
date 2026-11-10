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
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tree_sitter import Node


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.util.cbmc_clause_stripper import CbmcClauseSpan, find_cbmc_annotation_spans  # noqa: E402
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

# Calls whose arguments are not mutated: assertion and logging macros. Their operators are checks
# or debug output, not the program logic a contract is meant to pin down, and in many builds
# (e.g., lz4's default build) the macros expand to nothing, so the mutant is identical to the
# original program and can never be killed.
DEFAULT_SKIPPED_CALLS: frozenset[str] = frozenset({"assert", "DEBUGLOG"})

# A preprocessor line marker, e.g. `# 12 "lz4.c" 2` or `#line 12 "lz4.c"`.
_LINE_MARKER = re.compile(r"^#\s*(line\s+)?\d+")

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


def get_mutants(
    file_path: str, function_name: str, skipped_calls: frozenset[str] = frozenset()
) -> list[Mutant]:
    """Return mutants for the function with the given name in the file.

    Args:
        file_path (str): Path to the C source file.
        function_name (str): The function whose body should be mutated.
        skipped_calls (frozenset[str]): Names of calls (functions or function-like macros, e.g.
            `DEFAULT_SKIPPED_CALLS`) whose arguments are not mutated. Empty by default, which
            keeps the mutants the harness's mutation-testing tool shows the agent unchanged.

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
    in_body_annotation_spans = _get_in_body_cbmc_annotation_spans(target_function_body, source_code)

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
            if _is_inside_preprocessor_condition(node):
                continue
            if _is_inside_cprover_only_block(node, source_code):
                continue
            if skipped_calls and _is_inside_call_to(node, skipped_calls, source_code):
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


def _is_inside_preprocessor_condition(node: Node) -> bool:
    """Return True iff `node` is inside the condition of an `#if` or `#elif` directive.

    Such operators (e.g., the `&&` in `#if defined(_MSC_VER) && (_MSC_VER <= 1937)`) select a
    build configuration; they are not program logic a contract could constrain.

    Args:
        node (Node): The node to check.

    Returns:
        bool: True iff `node` lies within the condition of an enclosing `#if`/`#elif`.
    """
    ancestor = node.parent
    while ancestor is not None:
        if ancestor.type in ("preproc_if", "preproc_elif"):
            condition = ancestor.child_by_field_name("condition")
            if condition is not None and (
                condition.start_byte <= node.start_byte and node.end_byte <= condition.end_byte
            ):
                return True
        ancestor = ancestor.parent
    return False


_CPROVER_MACROS = frozenset({"__CPROVER", "__CPROVER__"})


def _is_inside_cprover_only_block(node: Node, source_code: bytes) -> bool:
    """Return True iff `node` is in code compiled only when CBMC's macros are defined.

    That is, inside `#ifdef __CPROVER__ ... (#else|#endif)` (or `__CPROVER`), or inside the
    `#else` branch of `#ifndef __CPROVER__`. Agents put spec scaffolding there (assumptions,
    snapshots of inputs for postconditions, loops that count cells for an `__CPROVER_assert`);
    it is part of the spec, not the program under test, so it is not mutated.

    Args:
        node (Node): The node to check.
        source_code (bytes): The source code the node was parsed from.

    Returns:
        bool: True iff `node` lies in a CBMC-only region of an enclosing `#ifdef`/`#ifndef`.
    """
    child = node
    ancestor = node.parent
    while ancestor is not None:
        if ancestor.type == "preproc_ifdef":
            name = ancestor.child_by_field_name("name")
            macro = source_code[name.start_byte : name.end_byte].decode("utf-8") if name else ""
            if macro in _CPROVER_MACROS:
                directive = ancestor.children[0].type  # "#ifdef" or "#ifndef"
                alternative = ancestor.child_by_field_name("alternative")
                in_alternative = alternative is not None and child.id == alternative.id
                if (directive == "#ifdef") != in_alternative:
                    return True
        child = ancestor
        ancestor = ancestor.parent
    return False


def _is_inside_call_to(node: Node, call_names: frozenset[str], source_code: bytes) -> bool:
    """Return True iff `node` is inside the arguments of a call to one of `call_names`.

    Tree-sitter parses a function-like macro invocation such as `assert(x < n)` or
    `DEBUGLOG(5, "%i", a - b)` as a `call_expression`, so this covers macros as well as functions.

    Args:
        node (Node): The node to check (e.g., a binary expression in a function body).
        call_names (frozenset[str]): The names of the calls to look for.
        source_code (bytes): The source code the node was parsed from.

    Returns:
        bool: True iff an ancestor of `node` is a call to one of `call_names`.
    """
    ancestor = node.parent
    while ancestor is not None:
        if ancestor.type == "call_expression":
            callee = ancestor.child_by_field_name("function")
            if callee is not None and callee.type == "identifier":
                name = source_code[callee.start_byte : callee.end_byte].decode("utf-8")
                if name in call_names:
                    return True
        ancestor = ancestor.parent
    return False


def drop_mutants_unchanged_after_preprocessing(
    mutants: list[Mutant], file_path: str, include_dirs: list[str] | None = None
) -> list[Mutant]:
    """Return the mutants whose preprocessed source differs from the original's.

    A mutation inside code the preprocessor removes (an `#if` branch that is not taken, or the
    argument of a macro that expands to nothing) leaves the compiled program unchanged, so no
    contract can kill it. Both the original and each mutant are preprocessed by `goto-cc -E` (the
    preprocessor CBMC uses) from the same path, so `__FILE__` expands identically; a single
    operator replacement does not move any line, so `__LINE__` does too.

    Args:
        mutants (list[Mutant]): Mutants of the file at `file_path`.
        file_path (str): Path to the original C source file.
        include_dirs (list[str] | None): Extra include directories, as passed to CBMC.

    Returns:
        list[Mutant]: The mutants that change the preprocessed program, in their original order.
    """
    source = Path(file_path).resolve()
    include_flags = [f"-I{d}" for d in [str(source.parent), *(include_dirs or [])]]
    with tempfile.TemporaryDirectory(prefix="avocado-preprocess-") as tmp:
        work_path = Path(tmp) / source.name

        def preprocess(text: str) -> str | None:
            work_path.write_text(text, encoding="utf-8")
            result = subprocess.run(
                ["goto-cc", "-E", *include_flags, work_path.name],
                cwd=tmp,
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                return None
            # Line markers (`# <line> "<file>" ...`) only locate code; drop them. Other `#` lines,
            # such as `#pragma CPROVER check ...`, can change what CBMC checks, so keep them.
            return "\n".join(l for l in result.stdout.splitlines() if not _LINE_MARKER.match(l))

        if shutil.which("goto-cc") is None:
            msg = "goto-cc is required to drop mutants that preprocess to the original program"
            raise RuntimeError(msg)
        original = preprocess(source.read_text(encoding="utf-8"))
        if original is None:
            # Cannot compare; keep every mutant rather than drop any on a preprocessor error.
            return list(mutants)
        return [m for m in mutants if preprocess(m.mutant_source) != original]


def _get_in_body_cbmc_annotation_spans(body: Node, source_code: bytes) -> list[CbmcClauseSpan]:
    """Return the in-body CBMC annotation spans.

    For example, given the following C function:

            uint16_t float2half(float flt)
                __CPROVER_assigns()
            {
                __CPROVER_assume(
                    __CPROVER_forall { unsigned int i; i < 512 !=> m__shift[i] < 32 });
                union {
                    float flt;
                    uint32_t num;
                } in;
                uint32_t n, j;
                in.flt = flt;
                n = in.num;
                j = (n >> 23) & 0x1ff;
                return (uint16_t)((uint32_t)m__base[j] + ((n & 0x007fffff) >> m__shift[j]));
            }

    The span for the `__CPROVER_assume` clause is returned.

    Args:
        body (Node): The body of the function in which to look for CBMC annotation spans.
        source_code (bytes): The source code of the file in which the function is declared.

    Returns:
        list[CbmcClauseSpan]: The in-body CBMC annotation spans.
    """
    return [
        s
        for s in find_cbmc_annotation_spans(source_code)
        if body.start_byte <= s.start_byte and s.end_byte <= body.end_byte
    ]


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
