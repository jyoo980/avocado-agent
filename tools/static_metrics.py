"""Static AST-only quality metrics for CBMC-annotated functions.

This module computes the metric bundle described in the spec-quality plan:

* M2.2 vacuity                (trivial clauses, empty `assigns` despite body writes)
* M3.2 lexical overlap        (Jaccard of spec identifiers vs body identifiers)
* M3.3 concrete-value bias    (literals vs symbolic terms in `ensures` clauses)
* M4.2 return-value coverage  (does any `ensures` mention `__CPROVER_return_value`?)
* M4.3 pointer-safety coverage (every dereferenced pointer parameter has a validity clause)
* M5.1 predicate-feature histogram (counts of `forall`, `is_fresh`, `__CPROVER_old`, ...)

All metrics are computed in a single tree-sitter pass. They require no CBMC run, so they
are cheap and run offline as part of the spec-quality dashboard.

Usage:
    avocado-static-metrics <PATH_TO_C_FILE_OR_DIRECTORY> [--jsonl PATH]

Without `--jsonl`, JSONL records are written to stdout.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
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

# Predicates we count for the M5.1 expressiveness histogram.
_PREDICATE_FEATURES = (
    "__CPROVER_forall",
    "__CPROVER_exists",
    "__CPROVER_old",
    "__CPROVER_is_fresh",
    "__CPROVER_pointer_equals",
    "__CPROVER_pointer_in_range_dfcc",
    "__CPROVER_obeys_contract",
    "__CPROVER_w_ok",
    "__CPROVER_r_ok",
    "__CPROVER_object_whole",
    "__CPROVER_return_value",
)

# Pointer-validity predicates considered to cover a pointer parameter for M4.3.
_POINTER_VALIDITY_PREDICATES = frozenset(
    {
        "__CPROVER_is_fresh",
        "__CPROVER_w_ok",
        "__CPROVER_r_ok",
        "__CPROVER_pointer_in_range_dfcc",
        "__CPROVER_pointer_equals",
        "__CPROVER_object_whole",
    }
)

# C operators and primitive type names that tree-sitter parses as `identifier` nodes when
# they appear inside `sizeof(...)` and similar argument-list contexts. Excluded everywhere we
# treat identifiers as "program identifiers" so they don't pollute lexical-overlap or
# concrete-value-bias counts.
_C_VOCAB_IDENTIFIERS = frozenset(
    {
        "sizeof",
        "int",
        "char",
        "short",
        "long",
        "unsigned",
        "signed",
        "float",
        "double",
        "void",
        "_Bool",
        "size_t",
        "ssize_t",
        "uint8_t",
        "uint16_t",
        "uint32_t",
        "uint64_t",
        "int8_t",
        "int16_t",
        "int32_t",
        "int64_t",
    }
)

# Identifier names we exclude from "spec identifiers" when computing lexical overlap with the
# body, because they are not real program identifiers — they are CBMC contract vocabulary
# whose presence does not indicate the spec is paraphrasing the implementation.
_SPEC_VOCAB_IDENTIFIERS = (
    frozenset(_PREDICATE_FEATURES) | _CONTRACT_CLAUSE_NAMES | _C_VOCAB_IDENTIFIERS
)


@dataclass(frozen=True)
class VacuityMetrics:
    """Trivial-clause and empty-assigns flags for M2.2."""

    trivial_clauses: list[str] = field(default_factory=list)
    empty_assigns_despite_writes: bool = False


@dataclass(frozen=True)
class LexicalOverlapMetrics:
    """M3.2 Jaccard overlap between spec identifiers and body identifiers."""

    spec_identifier_count: int = 0
    body_identifier_count: int = 0
    intersection_count: int = 0
    jaccard: float = 0.0


@dataclass(frozen=True)
class ConcreteValueBiasMetrics:
    """M3.3 ratio of concrete literals to total terms in `ensures` clauses."""

    literal_count: int = 0
    symbolic_count: int = 0
    literal_ratio: float = 0.0


@dataclass(frozen=True)
class ReturnValueCoverageMetrics:
    """M4.2 coverage of `__CPROVER_return_value` for non-`void` functions."""

    is_void: bool = False
    ensures_references_return_value: bool = False


@dataclass(frozen=True)
class PointerSafetyCoverageMetrics:
    """M4.3 coverage of pointer-parameter validity assertions."""

    pointer_params: list[str] = field(default_factory=list)
    dereferenced_in_body: list[str] = field(default_factory=list)
    covered_in_spec: list[str] = field(default_factory=list)
    uncovered: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class StaticFunctionMetrics:
    """All static metrics computed for a single annotated function."""

    file: str
    function: str
    vacuity: VacuityMetrics
    lexical_overlap: LexicalOverlapMetrics
    concrete_value_bias: ConcreteValueBiasMetrics
    return_value_coverage: ReturnValueCoverageMetrics
    pointer_safety_coverage: PointerSafetyCoverageMetrics
    feature_histogram: dict[str, int]


def compute_static_metrics(file_path: str) -> list[StaticFunctionMetrics]:
    """Compute the static metric bundle for every annotated function in a C file.

    Functions without any `__CPROVER_*` clauses are skipped — there is nothing to score.

    Args:
        file_path (str): Path to a C source file.

    Returns:
        list[StaticFunctionMetrics]: One record per annotated function. Order matches source
            order to keep output deterministic.
    """
    content_bytes = Path(file_path).read_bytes()
    tree = _PARSER.parse(content_bytes)

    metrics: list[StaticFunctionMetrics] = []
    for fn_def in _function_definitions(tree.root_node):
        clauses = _extract_clauses(fn_def)
        if not clauses:
            continue
        name = _function_name(fn_def)
        if name is None:
            continue
        body = fn_def.child_by_field_name("body")
        return_type_text = _return_type_text(fn_def, content_bytes)
        params = _extract_parameters(fn_def, content_bytes)

        metrics.append(
            StaticFunctionMetrics(
                file=file_path,
                function=name,
                vacuity=_compute_vacuity(clauses, body, content_bytes),
                lexical_overlap=_compute_lexical_overlap(clauses, body, content_bytes),
                concrete_value_bias=_compute_concrete_value_bias(clauses),
                return_value_coverage=_compute_return_value_coverage(return_type_text, clauses),
                pointer_safety_coverage=_compute_pointer_safety_coverage(
                    params, body, clauses, content_bytes
                ),
                feature_histogram=_compute_feature_histogram(clauses),
            )
        )
    return metrics


def main() -> int:
    """CLI entry point. Walks files, prints/writes one JSONL record per annotated function.

    Returns:
        int: 0 on success, 1 when no `.c` files were found.
    """
    parser = argparse.ArgumentParser(
        description="Compute static AST-only quality metrics for CBMC-annotated functions."
    )
    parser.add_argument("path", help="Path to a C file, or a directory containing C files.")
    parser.add_argument(
        "--jsonl",
        default=None,
        help="Write JSONL records to this file. Defaults to stdout.",
    )
    args = parser.parse_args()

    files = _collect_c_files(args.path)
    if not files:
        print(f"No .c files found at: {args.path}", file=sys.stderr)
        return 1

    output = (
        Path(args.jsonl).open("w", encoding="utf-8")  # noqa: SIM115 — closed in finally below
        if args.jsonl
        else sys.stdout
    )
    try:
        for file in files:
            for record in compute_static_metrics(str(file)):
                output.write(json.dumps({"kind": "static_metrics", **asdict(record)}) + "\n")
    finally:
        if args.jsonl:
            output.close()
    return 0


def _collect_c_files(path_str: str) -> list[Path]:
    """Return the list of C files to scan for a given path (file or directory).

    Args:
        path_str (str): A path to a `.c` file or a directory.

    Returns:
        list[Path]: Sorted list of `.c` files; empty when the input is missing or
            contains no `.c` files.
    """
    path = Path(path_str)
    if not path.exists():
        return []
    if path.is_dir():
        return sorted(path.rglob("*.c"))
    return [path] if path.suffix == ".c" else []


def _function_definitions(root: Node) -> Iterator[Node]:
    """Yield every `function_definition` node in DFS order.

    Args:
        root (Node): The AST root.

    Yields:
        Iterator[Node]: `function_definition` nodes in source order.
    """
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type == "function_definition":
            yield node
        stack.extend(reversed(node.children))


def _function_name(fn_def: Node) -> str | None:
    """Return the function's name or None if the AST is too malformed to recover one.

    Args:
        fn_def (Node): A `function_definition` node.

    Returns:
        str | None: The function name when recoverable, else None.
    """
    declarator = _find_function_declarator(fn_def)
    if declarator is None:
        return None
    while declarator.type != "identifier":
        inner = declarator.child_by_field_name("declarator")
        if inner is None:
            return None
        declarator = inner
    return declarator.text.decode("utf-8") if declarator.text else None


def _find_function_declarator(fn_def: Node) -> Node | None:
    """Return the `function_declarator` for a definition, recovering from misshapen parses.

    Tree-sitter sometimes wraps the real `function_declarator` in an `array_declarator` (when
    a contract clause contains a subscript expression like `arr[0]`) or buries it inside an
    `ERROR` child (some clause shapes confuse the C grammar). Recover by DFS-searching for
    the first `function_declarator` descendant.

    Args:
        fn_def (Node): The `function_definition` node.

    Returns:
        Node | None: The recovered `function_declarator`, or the original declarator child
            when no recovery is possible.
    """
    declared = fn_def.child_by_field_name("declarator")
    if declared is not None and declared.type == "function_declarator":
        return declared
    for descendant in _walk(fn_def):
        if descendant.type == "function_declarator":
            return descendant
    return declared


def _extract_clauses(fn_def: Node) -> list[Node]:
    """Return the contract-clause `call_expression` nodes attached to a function definition.

    A contract clause is a `call_expression` whose callee identifier is one of the
    `__CPROVER_*` clause names (requires/ensures/assigns/frees). They are children of the
    function_declarator (current tree-sitter-c) but defensive code also looks under any
    sibling ERROR nodes.

    Args:
        fn_def (Node): The `function_definition` node.

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
    """Return the clause kind (`requires`, `ensures`, `assigns`, `frees`).

    Args:
        clause (Node): A contract-clause `call_expression` node.

    Returns:
        str: The clause kind string with the `__CPROVER_` prefix removed.
    """
    fn = clause.child_by_field_name("function")
    assert fn is not None and fn.text is not None
    return fn.text.decode("utf-8").removeprefix("__CPROVER_")


def _clause_args(clause: Node) -> list[Node]:
    """Return the argument nodes of a clause, skipping the `(`/`,`/`)` punctuation tokens.

    Args:
        clause (Node): A contract-clause `call_expression` node.

    Returns:
        list[Node]: The clause's argument expression nodes in source order.
    """
    args = clause.child_by_field_name("arguments")
    if args is None:
        return []
    return [c for c in args.children if c.type not in {"(", ",", ")"}]


def _return_type_text(fn_def: Node, content: bytes) -> str:
    """Return the textual return type of a function (best-effort).

    Args:
        fn_def (Node): The `function_definition` node.
        content (bytes): The full file content.

    Returns:
        str: The return type as written in source, or empty string when missing.
    """
    type_node = fn_def.child_by_field_name("type")
    if type_node is None:
        return ""
    return content[type_node.start_byte : type_node.end_byte].decode("utf-8").strip()


def _extract_parameters(fn_def: Node, content: bytes) -> list[dict[str, object]]:
    """Return a list of `{name, is_pointer}` dicts for the function's parameters.

    Args:
        fn_def (Node): The `function_definition` node.
        content (bytes): The full file content.

    Returns:
        list[dict[str, object]]: One dict per parameter with keys `name` and `is_pointer`.
    """
    declarator = _find_function_declarator(fn_def)
    if declarator is None:
        return []
    parameter_list = None
    for child in declarator.children:
        if child.type == "parameter_list":
            parameter_list = child
            break
    if parameter_list is None:
        return []
    out: list[dict[str, object]] = []
    for child in parameter_list.children:
        if child.type != "parameter_declaration":
            continue
        param_decl = child.child_by_field_name("declarator")
        if param_decl is None:
            continue
        name, is_pointer = _resolve_param_name(param_decl)
        if name is not None:
            _ = content  # parameter content already captured by tree node positions
            out.append({"name": name, "is_pointer": is_pointer})
    return out


def _resolve_param_name(declarator: Node) -> tuple[str | None, bool]:
    """Return `(name, is_pointer)` for a parameter declarator.

    Treats both `pointer_declarator` (e.g. `int *p`) and `array_declarator` (e.g. `int p[]`)
    as pointers — they both decay to pointers as parameters and are equally subject to
    pointer-safety predicates in CBMC contracts.

    Args:
        declarator (Node): The parameter's declarator node.

    Returns:
        tuple[str | None, bool]: The parameter name (or None if unrecoverable) and a flag
            indicating whether the parameter has pointer-decayed type.
    """
    is_pointer = False
    while True:
        if declarator.type in {"pointer_declarator", "array_declarator"}:
            is_pointer = True
            inner = declarator.child_by_field_name("declarator")
            if inner is None:
                return None, is_pointer
            declarator = inner
        elif declarator.type == "identifier":
            return (declarator.text.decode("utf-8") if declarator.text else None), is_pointer
        elif declarator.type == "parenthesized_declarator":
            # e.g., function-pointer parameters like `void (*f)(void)`
            inner = next((c for c in declarator.children if c.type != "(" and c.type != ")"), None)
            if inner is None:
                return None, is_pointer
            declarator = inner
        else:
            inner = declarator.child_by_field_name("declarator")
            if inner is None:
                return None, is_pointer
            declarator = inner


def _walk(node: Node) -> Iterator[Node]:
    """DFS iterator over a node and its descendants.

    Args:
        node (Node): The root of the traversal.

    Yields:
        Iterator[Node]: Each node in the subtree, including `node` itself.
    """
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(n.children)


def _identifiers_in(node: Node) -> list[str]:
    """Return identifier names appearing in (and under) a node, with duplicates.

    Args:
        node (Node): The subtree root to scan.

    Returns:
        list[str]: Identifier names in DFS order; duplicates are preserved so callers can
            choose to count or de-duplicate.
    """
    return [
        n.text.decode("utf-8") for n in _walk(node) if n.type == "identifier" and n.text is not None
    ]


def _node_text(node: Node, content: bytes) -> str:
    """Return the source text spanned by a node, normalized for inner whitespace.

    Args:
        node (Node): The node whose source text to extract.
        content (bytes): The full file content.

    Returns:
        str: The node's source text with internal whitespace runs collapsed to single
            spaces.
    """
    return " ".join(content[node.start_byte : node.end_byte].decode("utf-8").split())


# ----- M2.2 -----


def _compute_vacuity(clauses: list[Node], body: Node | None, content: bytes) -> VacuityMetrics:
    """Compute vacuity flags. See M2.2.

    A clause is trivial when its single argument is the literal `1`/`true` or a
    syntactically self-equal binary expression (e.g., `p == p`).

    Args:
        clauses (list[Node]): Contract clauses to inspect.
        body (Node | None): The function body, used to flag empty `assigns` clauses on
            functions that actually write through pointers.
        content (bytes): The full file content.

    Returns:
        VacuityMetrics: Triviality-related flags.
    """
    trivial: list[str] = []
    has_empty_assigns = False
    for clause in clauses:
        kind = _clause_kind(clause)
        args = _clause_args(clause)
        if kind == "assigns" and len(args) == 0:
            has_empty_assigns = True
        if kind in {"requires", "ensures"} and len(args) == 1:
            arg = args[0]
            if _is_trivial_boolean(arg, content):
                trivial.append(_node_text(clause, content))
    empty_assigns_despite_writes = has_empty_assigns and (
        body is not None and _body_writes_through_pointer(body)
    )
    return VacuityMetrics(
        trivial_clauses=trivial,
        empty_assigns_despite_writes=empty_assigns_despite_writes,
    )


def _is_trivial_boolean(node: Node, content: bytes) -> bool:
    """Return True iff `node` is a syntactically obvious tautology.

    Args:
        node (Node): The boolean expression to test.
        content (bytes): The full file content.

    Returns:
        bool: True iff `node` is `1`, `true`, or a self-equal `==` comparison.
    """
    text = _node_text(node, content).strip()
    if text in {"1", "(1)", "true"}:
        return True
    # Strip a single layer of redundant parens to catch `(p == p)`.
    inner = node
    if (
        inner.type == "parenthesized_expression"
        and len(inner.children) == 3
        and inner.children[1].type not in {"(", ")"}
    ):
        inner = inner.children[1]
    if inner.type == "binary_expression":
        op_node = inner.child_by_field_name("operator")
        left = inner.child_by_field_name("left")
        right = inner.child_by_field_name("right")
        if op_node is not None and left is not None and right is not None:
            op = content[op_node.start_byte : op_node.end_byte].decode("utf-8")
            if op == "==" and _node_text(left, content) == _node_text(right, content):
                return True
    return False


def _body_writes_through_pointer(body: Node) -> bool:
    """Return True iff the body contains an assignment whose LHS dereferences a pointer.

    Args:
        body (Node): The function body's `compound_statement` node.

    Returns:
        bool: True iff any `assignment_expression` in the body writes through a pointer
            (`*p = ...`, `p[i] = ...`, or `p->x = ...`).
    """
    for n in _walk(body):
        if n.type != "assignment_expression":
            continue
        lhs = n.child_by_field_name("left")
        if lhs is None:
            continue
        if lhs.type in {"pointer_expression", "subscript_expression", "field_expression"}:
            return True
    return False


# ----- M3.2 -----


def _compute_lexical_overlap(
    clauses: list[Node], body: Node | None, content: bytes
) -> LexicalOverlapMetrics:
    """Compute Jaccard overlap of identifiers between spec clauses and the body.

    Args:
        clauses (list[Node]): The function's contract-clause nodes.
        body (Node | None): The function body's `compound_statement` node, when present.
        content (bytes): The full file content (unused here; kept for API symmetry).

    Returns:
        LexicalOverlapMetrics: Spec/body identifier counts, intersection, and Jaccard.
    """
    _ = content  # identifiers are read off node texts directly
    spec_ids: set[str] = set()
    for clause in clauses:
        for arg in _clause_args(clause):
            for name in _identifiers_in(arg):
                if name not in _SPEC_VOCAB_IDENTIFIERS:
                    spec_ids.add(name)
    body_ids: set[str] = (
        {n for n in _identifiers_in(body) if n not in _C_VOCAB_IDENTIFIERS}
        if body is not None
        else set()
    )
    intersection = spec_ids & body_ids
    union = spec_ids | body_ids
    jaccard = (len(intersection) / len(union)) if union else 0.0
    return LexicalOverlapMetrics(
        spec_identifier_count=len(spec_ids),
        body_identifier_count=len(body_ids),
        intersection_count=len(intersection),
        jaccard=round(jaccard, 4),
    )


# ----- M3.3 -----


_LITERAL_NODE_TYPES = frozenset(
    {"number_literal", "char_literal", "string_literal", "true", "false", "null"}
)


def _compute_concrete_value_bias(clauses: list[Node]) -> ConcreteValueBiasMetrics:
    """Compute the literal-vs-symbolic ratio across all `ensures` clause expressions.

    Args:
        clauses (list[Node]): The function's contract-clause nodes.

    Returns:
        ConcreteValueBiasMetrics: Literal/symbolic counts and the literal ratio.
    """
    literal = 0
    symbolic = 0
    for clause in clauses:
        if _clause_kind(clause) != "ensures":
            continue
        for arg in _clause_args(clause):
            for n in _walk(arg):
                if n.type in _LITERAL_NODE_TYPES:
                    literal += 1
                elif n.type == "identifier" and n.text:
                    name = n.text.decode("utf-8")
                    if name not in _SPEC_VOCAB_IDENTIFIERS:
                        symbolic += 1
    total = literal + symbolic
    ratio = (literal / total) if total else 0.0
    return ConcreteValueBiasMetrics(
        literal_count=literal,
        symbolic_count=symbolic,
        literal_ratio=round(ratio, 4),
    )


# ----- M4.2 -----


def _compute_return_value_coverage(
    return_type_text: str, clauses: list[Node]
) -> ReturnValueCoverageMetrics:
    """Flag whether non-`void` functions actually constrain their return value.

    Args:
        return_type_text (str): The function's return type as written in source.
        clauses (list[Node]): The function's contract-clause nodes.

    Returns:
        ReturnValueCoverageMetrics: A `is_void` flag plus whether any `ensures` clause
            references `__CPROVER_return_value`.
    """
    is_void = return_type_text.replace(" ", "") == "void"
    references_return = False
    for clause in clauses:
        if _clause_kind(clause) != "ensures":
            continue
        for arg in _clause_args(clause):
            if any(name == "__CPROVER_return_value" for name in _identifiers_in(arg)):
                references_return = True
                break
        if references_return:
            break
    return ReturnValueCoverageMetrics(
        is_void=is_void,
        ensures_references_return_value=references_return,
    )


# ----- M4.3 -----


def _compute_pointer_safety_coverage(
    params: list[dict[str, object]],
    body: Node | None,
    clauses: list[Node],
    content: bytes,
) -> PointerSafetyCoverageMetrics:
    """Compute which dereferenced pointer parameters are covered by validity predicates.

    Args:
        params (list[dict[str, object]]): Parameters as returned by `_extract_parameters`.
        body (Node | None): The function body.
        clauses (list[Node]): The function's contract-clause nodes.
        content (bytes): The full file content (unused; kept for API symmetry).

    Returns:
        PointerSafetyCoverageMetrics: Coverage breakdown of pointer parameters.
    """
    _ = content
    pointer_params = [str(p["name"]) for p in params if p["is_pointer"]]
    pointer_param_set = set(pointer_params)

    deref: set[str] = set()
    if body is not None:
        for n in _walk(body):
            if n.type == "pointer_expression":
                # *p — the operand identifier
                operand = next((c for c in n.children if c.type == "identifier"), None)
                if operand and operand.text:
                    name = operand.text.decode("utf-8")
                    if name in pointer_param_set:
                        deref.add(name)
            elif n.type == "subscript_expression":
                arg = n.child_by_field_name("argument")
                if arg is not None and arg.type == "identifier" and arg.text:
                    name = arg.text.decode("utf-8")
                    if name in pointer_param_set:
                        deref.add(name)

    covered: set[str] = set()
    for clause in clauses:
        for n in _walk(clause):
            if n.type != "call_expression":
                continue
            fn = n.child_by_field_name("function")
            if fn is None or fn.type != "identifier" or not fn.text:
                continue
            if fn.text.decode("utf-8") not in _POINTER_VALIDITY_PREDICATES:
                continue
            args = n.child_by_field_name("arguments")
            if args is None:
                continue
            # The pointer being asserted is always the first non-punctuation arg.
            first_arg = next((c for c in args.children if c.type not in {"(", ",", ")"}), None)
            if first_arg is None:
                continue
            for ident_name in _identifiers_in(first_arg):
                if ident_name in pointer_param_set:
                    covered.add(ident_name)

    uncovered = sorted(deref - covered)
    return PointerSafetyCoverageMetrics(
        pointer_params=sorted(pointer_param_set),
        dereferenced_in_body=sorted(deref),
        covered_in_spec=sorted(covered),
        uncovered=uncovered,
    )


# ----- M5.1 -----


def _compute_feature_histogram(clauses: list[Node]) -> dict[str, int]:
    """Count occurrences of CBMC predicate features across all clause bodies.

    Args:
        clauses (list[Node]): The function's contract-clause nodes.

    Returns:
        dict[str, int]: Map from predicate name to occurrence count, including the four
            outer clause kinds.
    """
    counts = dict.fromkeys(_PREDICATE_FEATURES, 0)
    counts["__CPROVER_requires"] = 0
    counts["__CPROVER_ensures"] = 0
    counts["__CPROVER_assigns"] = 0
    counts["__CPROVER_frees"] = 0
    for clause in clauses:
        kind = _clause_kind(clause)
        counts[f"__CPROVER_{kind}"] += 1
        for n in _walk(clause):
            if n.type == "identifier" and n.text:
                name = n.text.decode("utf-8")
                if name in _PREDICATE_FEATURES:
                    counts[name] += 1
    return counts


if __name__ == "__main__":
    sys.exit(main())
