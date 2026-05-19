"""Tests for the CBMC clause stripper."""

from pathlib import Path

import tree_sitter_c as tsc
from tree_sitter import Language, Parser

from tools.util.cbmc_clause_stripper import (
    CBMC_CLAUSE_NAMES,
    CbmcClauseSpan,
    find_cbmc_annotation_spans,
    strip_cbmc_clauses,
)

_PARSER = Parser(Language(tsc.language()))


def test_strip_preserves_byte_length_and_newlines() -> None:
    source = (
        b"void f(int* a)\n"
        b"__CPROVER_requires(a != 0)\n"
        b"{\n"
        b"    *a = 1;\n"
        b"}\n"
    )
    stripped, spans = strip_cbmc_clauses(source)

    assert len(stripped) == len(source)
    assert stripped.count(b"\n") == source.count(b"\n")
    assert len(spans) == 1
    assert spans[0].kind == "__CPROVER_requires"
    assert source[spans[0].start_byte : spans[0].end_byte] == b"__CPROVER_requires(a != 0)"
    assert stripped[spans[0].start_byte : spans[0].end_byte] == b" " * (
        spans[0].end_byte - spans[0].start_byte
    )


def test_strip_handles_all_four_clause_macros() -> None:
    source = (
        b"void f(int* a)\n"
        b"__CPROVER_requires(a != 0)\n"
        b"__CPROVER_ensures(*a == 1)\n"
        b"__CPROVER_assigns(*a)\n"
        b"__CPROVER_frees()\n"
        b"{ *a = 1; }\n"
    )
    stripped, spans = strip_cbmc_clauses(source)
    kinds = [s.kind for s in spans]
    assert kinds == list(CBMC_CLAUSE_NAMES)
    assert b"__CPROVER_" not in stripped


def test_strip_handles_cprover_forall_with_inner_braces() -> None:
    source = (
        b"int f(int arr[], int n)\n"
        b"__CPROVER_ensures(__CPROVER_forall {\n"
        b"    int k;\n"
        b"    (0 <= k && k < n) ==> arr[k] <= arr[k + 1]\n"
        b"})\n"
        b"{ return 0; }\n"
    )
    stripped, spans = strip_cbmc_clauses(source)

    assert len(spans) == 1
    clause_text = source[spans[0].start_byte : spans[0].end_byte].decode()
    assert clause_text.startswith("__CPROVER_ensures(__CPROVER_forall {")
    assert clause_text.endswith("})")
    # All inner-brace lines must have been blanked to spaces, but newlines preserved.
    inside = stripped[spans[0].start_byte : spans[0].end_byte]
    for b in inside:
        assert b in (ord(" "), ord("\n"))


def test_strip_ignores_clause_lookalikes_in_comments_and_strings() -> None:
    source = (
        b'const char *s = "__CPROVER_requires(bad)";\n'
        b"// __CPROVER_ensures(this is a comment)\n"
        b"/* __CPROVER_assigns(also a comment) */\n"
        b"void f(void) {}\n"
    )
    stripped, spans = strip_cbmc_clauses(source)
    assert spans == []
    assert stripped == source


def test_strip_leaves_non_clause_cprover_helpers_alone() -> None:
    # `__CPROVER_old`, `__CPROVER_is_fresh`, `__CPROVER_return_value`, `__CPROVER_forall` only
    # appear *inside* clauses we already strip. They must never be removed in isolation.
    source = (
        b"int x = __CPROVER_is_fresh(p, 4);\n"
        b"int y = __CPROVER_old(z);\n"
    )
    stripped, spans = strip_cbmc_clauses(source)
    assert spans == []
    assert stripped == source


def test_strip_does_not_match_substring_inside_other_identifier() -> None:
    source = b"int my__CPROVER_requires_helper(int x) { return x; }\n"
    stripped, spans = strip_cbmc_clauses(source)
    assert spans == []
    assert stripped == source


def test_stripped_quicksort_benchmark_parses_without_errors() -> None:
    source = Path("eval/benchmarks/quicksort/quicksort.c").read_bytes()
    stripped, spans = strip_cbmc_clauses(source)

    tree = _PARSER.parse(stripped)
    assert not tree.root_node.has_error

    names: list[str] = []
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type == "function_definition":
            decl = node.child_by_field_name("declarator")
            while decl is not None and decl.type != "identifier":
                decl = decl.child_by_field_name("declarator")
            if decl is not None and decl.text is not None:
                names.append(decl.text.decode())
        stack.extend(node.children)
    assert set(names) == {"swap", "partition", "quickSort"}
    assert {s.kind for s in spans} <= set(CBMC_CLAUSE_NAMES)


def test_clause_span_byte_ranges_recover_original_text() -> None:
    source = Path("test/data/quicksort_with_forall_subscript.c").read_bytes()
    _, spans = strip_cbmc_clauses(source)
    for span in spans:
        text = source[span.start_byte : span.end_byte]
        assert text.startswith(span.kind.encode())
        assert text.endswith(b")")


def test_clause_spans_attributable_to_owning_function_by_byte_range() -> None:
    # Regression for the quicksort.c bug: clauses for `partition` and `quickSort` must be
    # attributable to their respective functions and not bleed across.
    source = Path("eval/benchmarks/quicksort/quicksort.c").read_bytes()
    stripped, spans = strip_cbmc_clauses(source)

    tree = _PARSER.parse(stripped)
    fn_ranges: dict[str, tuple[int, int, int]] = {}
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type == "function_definition":
            decl = node.child_by_field_name("declarator")
            while decl is not None and decl.type != "identifier":
                decl = decl.child_by_field_name("declarator")
            body = node.child_by_field_name("body")
            full_decl = node.child_by_field_name("declarator")
            if decl is not None and decl.text is not None and body is not None and full_decl is not None:
                fn_ranges[decl.text.decode()] = (
                    full_decl.end_byte,
                    body.start_byte,
                    body.end_byte,
                )
        stack.extend(node.children)

    def clauses_in(fn: str) -> list[CbmcClauseSpan]:
        gap_start, gap_end, _ = fn_ranges[fn]
        return [s for s in spans if gap_start <= s.start_byte < gap_end]

    partition_kinds = [s.kind for s in clauses_in("partition")]
    quicksort_kinds = [s.kind for s in clauses_in("quickSort")]
    assert partition_kinds == [
        "__CPROVER_requires",
        "__CPROVER_requires",
        "__CPROVER_assigns",
        "__CPROVER_ensures",
        "__CPROVER_ensures",
        "__CPROVER_ensures",
        "__CPROVER_ensures",
    ]
    assert quicksort_kinds == [
        "__CPROVER_requires",
        "__CPROVER_requires",
        "__CPROVER_requires",
        "__CPROVER_assigns",
        "__CPROVER_ensures",
    ]


def test_find_cbmc_annotation_spans_covers_in_body_assume_with_forall() -> None:
    source = (
        b"void f(int* a)\n"
        b"__CPROVER_assigns()\n"
        b"{\n"
        b"    __CPROVER_assume(\n"
        b"        __CPROVER_forall { unsigned int i; i < 512 ==> a[i] < 32 });\n"
        b"    *a = 1;\n"
        b"}\n"
    )
    spans = find_cbmc_annotation_spans(source)
    kinds = [s.kind for s in spans]
    assert kinds == ["__CPROVER_assigns", "__CPROVER_assume"]

    assume_span = spans[1]
    assume_text = source[assume_span.start_byte : assume_span.end_byte].decode()
    assert assume_text.startswith("__CPROVER_assume(")
    assert assume_text.endswith("})")
    assert "i < 512" in assume_text


def test_find_cbmc_annotation_spans_skips_identifiers_without_parens() -> None:
    # `__CPROVER_return_value` has no following `(`, so the scanner must not produce a span for it.
    source = (
        b"int f(int* a)\n"
        b"__CPROVER_ensures(__CPROVER_return_value == 1)\n"
        b"{\n"
        b"    return 1;\n"
        b"}\n"
    )
    spans = find_cbmc_annotation_spans(source)
    kinds = [s.kind for s in spans]
    assert kinds == ["__CPROVER_ensures"]
