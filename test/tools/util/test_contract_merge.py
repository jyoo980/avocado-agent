"""Tests for `tools.util.contract_merge`: locating spans and splicing one function's work back."""

from pathlib import Path

from tools.util.contract_merge import (
    find_function_span,
    iter_top_level_items,
    merge_function,
)
from tools.util.tree_sitter_utils import _parse_to_ast, get_function_declarator, get_function_definition

BASE = b"""#include <stdio.h>
#define CAP 16
struct S { int a; };
static int helper_old(int x) { return x; }
#ifdef FOO
static int hidden(int x) { return x; }
#endif
int f(int a)
{
    return a;
}
char *pr(char *p) { return p; }
int g(int b) { return b; }
"""


def _contract_text(source: bytes, function: str) -> bytes:
    """Return the text between `function`'s declarator and its body in `source`."""
    tree = _parse_to_ast(source)
    node = get_function_definition(tree.root_node, function)
    assert node is not None
    declarator = get_function_declarator(node)
    body = node.child_by_field_name("body")
    assert declarator is not None and body is not None
    return source[declarator.end_byte : body.start_byte]


def test_top_level_items_keys_flattening_and_original_text() -> None:
    items = iter_top_level_items(BASE)
    keys = [item.key for item in items]
    assert ("macro", "CAP") in keys
    assert ("struct_specifier", "S") in keys
    assert ("function", "hidden") in keys
    hidden = next(item for item in items if item.key == ("function", "hidden"))
    assert hidden.guard == ("#ifdef FOO",)
    assert all(item.kind != "comment" for item in items)
    # The struct's trailing `;` is glued back on, and text comes from the original bytes.
    struct = next(item for item in items if item.key == ("struct_specifier", "S"))
    assert struct.text == b"struct S { int a; };"
    annotated = BASE.replace(b"int f(int a)\n{", b"int f(int a)\n__CPROVER_requires(a > 0)\n{")
    f_item = next(item for item in iter_top_level_items(annotated) if item.key == ("function", "f"))
    assert b"__CPROVER_requires(a > 0)" in f_item.text


def test_find_function_span_covers_pointer_return_and_kr_style() -> None:
    kr = BASE + b"int kr(a, b) int a; int b; { return a + b; }\n"
    for function, prefix in (("pr", b"char *pr("), ("kr", b"int kr(a, b)"), ("f", b"int f(int a)")):
        span = find_function_span(kr, function)
        assert span is not None, function
        assert kr[span.start_byte :].startswith(prefix)
        assert kr[span.body_start_byte] == ord("{")
        assert kr[span.end_byte - 1] == ord("}")
    hidden = find_function_span(BASE, "hidden")
    assert hidden is not None and hidden.guard == ("#ifdef FOO",)


def test_find_function_span_rejects_missing_duplicate_and_error_wrapped() -> None:
    assert find_function_span(BASE, "nope") is None
    assert find_function_span(BASE + b"int f(int a) { return 0; }\n", "f") is None
    # tree-sitter's recovery starts `q`'s node inside the broken declaration before it.
    broken = b"int g __attribute__((unused));\nvoid q(void) { }\n"
    assert find_function_span(broken, "q") is None


def test_merge_contract_only_change_lands_between_declarator_and_body() -> None:
    snapshot = BASE.replace(
        b"int f(int a)\n{", b"int f(int a)\n__CPROVER_requires(a > 0)\n__CPROVER_ensures(1)\n{"
    )
    merged, report = merge_function(canonical=BASE, snapshot=snapshot, fork_base=BASE, function="f")
    assert report.merged and not report.transplanted and not report.dropped
    assert b"__CPROVER_requires(a > 0)" in _contract_text(merged, "f")
    # Everything else is byte-identical.
    assert merged.replace(b"__CPROVER_requires(a > 0)\n__CPROVER_ensures(1)\n", b"") == BASE


def test_merge_transplants_helper_and_define_before_the_function_in_order() -> None:
    snapshot = BASE.replace(
        b"int f(int a)\n{",
        b"#define FBOUND 8\nstatic int spec_ok(int a) { return a > 0; }\n"
        b"int f(int a)\n__CPROVER_requires(spec_ok(a) && a < FBOUND)\n{",
    )
    merged, report = merge_function(canonical=BASE, snapshot=snapshot, fork_base=BASE, function="f")
    assert report.merged
    assert report.transplanted == ["macro:FBOUND", "function:spec_ok"]
    define_at = merged.index(b"#define FBOUND 8")
    helper_at = merged.index(b"static int spec_ok")
    f_at = merged.index(b"int f(int a)")
    assert define_at < helper_at < f_at
    # `pr` and `g` are untouched and still follow `f`.
    assert merged.index(b"char *pr(") > f_at
    assert merged.endswith(b"int g(int b) { return b; }\n")


def test_merge_carries_body_edit_and_reports_it() -> None:
    snapshot = BASE.replace(b"{\n    return a;\n}", b"{\n    __CPROVER_assert(a >= 0, \"a\");\n    return a;\n}")
    merged, report = merge_function(canonical=BASE, snapshot=snapshot, fork_base=BASE, function="f")
    assert report.merged and report.body_changed
    assert b"__CPROVER_assert(a >= 0" in merged


def test_merge_drops_edits_to_other_functions_and_declarations() -> None:
    snapshot = (
        BASE.replace(b"int g(int b) { return b; }", b"int g(int b) { return b + 1; }")
        .replace(b"#define CAP 16", b"#define CAP 32")
        .replace(b"int f(int a)\n{", b"int f(int a)\n__CPROVER_ensures(1)\n{")
    )
    merged, report = merge_function(canonical=BASE, snapshot=snapshot, fork_base=BASE, function="f")
    assert report.merged
    assert sorted(report.dropped) == ["function:g", "macro:CAP"]
    assert b"int g(int b) { return b; }" in merged
    assert b"#define CAP 16" in merged
    assert b"__CPROVER_ensures(1)" in _contract_text(merged, "f")


def test_merge_fails_on_helper_with_different_text_and_skips_identical_helper() -> None:
    helper = b"static int spec_ok(int a) { return a > 0; }\n"
    snapshot = BASE.replace(b"int f(int a)\n{", helper + b"int f(int a)\n__CPROVER_requires(spec_ok(a))\n{")
    canonical = BASE.replace(b"int g(int b)", helper + b"int g(int b)")  # another session added it
    merged, report = merge_function(canonical=canonical, snapshot=snapshot, fork_base=BASE, function="f")
    assert report.merged and report.skipped_duplicates == ["function:spec_ok"]
    assert merged.count(b"static int spec_ok") == 1
    clashing = canonical.replace(b"return a > 0;", b"return a >= 0;")
    merged, report = merge_function(canonical=clashing, snapshot=snapshot, fork_base=BASE, function="f")
    assert not report.merged and "spec_ok" in report.reason
    assert merged == clashing


def test_merge_after_a_prior_merge_keeps_both_contracts() -> None:
    # Session for `g` landed first, shifting every later offset in the canonical file.
    canonical = BASE.replace(
        b"int g(int b) { return b; }", b"int g(int b)\n__CPROVER_ensures(__CPROVER_return_value == b)\n{ return b; }"
    )
    snapshot = BASE.replace(b"int f(int a)\n{", b"int f(int a)\n__CPROVER_ensures(__CPROVER_return_value == a)\n{")
    merged, report = merge_function(canonical=canonical, snapshot=snapshot, fork_base=BASE, function="f")
    assert report.merged and not report.dropped
    assert b"__CPROVER_return_value == a" in _contract_text(merged, "f")
    assert b"__CPROVER_return_value == b" in _contract_text(merged, "g")


def test_merge_function_inside_preproc_block() -> None:
    snapshot = BASE.replace(
        b"static int hidden(int x) { return x; }",
        b"static int hidden(int x)\n__CPROVER_requires(x > 0)\n{ return x; }",
    )
    merged, report = merge_function(canonical=BASE, snapshot=snapshot, fork_base=BASE, function="hidden")
    assert report.merged
    block = merged[merged.index(b"#ifdef FOO") : merged.index(b"#endif")]
    assert b"__CPROVER_requires(x > 0)" in block


def test_merge_fails_when_the_function_was_renamed_or_removed() -> None:
    snapshot = BASE.replace(b"int f(int a)", b"int f2(int a)")
    merged, report = merge_function(canonical=BASE, snapshot=snapshot, fork_base=BASE, function="f")
    assert not report.merged and merged == BASE


def test_merge_on_the_quicksort_fixture_round_trips_a_stripped_copy() -> None:
    original = Path("test/data/quicksort.c").read_bytes()
    from tools.util.cbmc_clause_stripper import strip_cbmc_clauses

    stripped, _ = strip_cbmc_clauses(original)
    canonical = stripped
    for function in ("swap", "partition", "quickSort"):
        # The "session" is the original file: it carries that function's real contract.
        canonical, report = merge_function(
            canonical=canonical, snapshot=original, fork_base=stripped, function=function
        )
        assert report.merged, (function, report.reason)
    for function in ("swap", "partition", "quickSort"):
        assert _contract_text(canonical, function).strip() == _contract_text(original, function).strip()
