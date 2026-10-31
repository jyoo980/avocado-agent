"""Tests for `tools.util.edit_rules`: which edits to a session's copy the merge may keep."""

from pathlib import Path

from tools.util import edit_rules
from tools.util.edit_rules import (
    check_edits,
    frozen_functions_for,
    function_body_offsets,
    function_header,
    function_line,
    functions_with_changed_contracts,
    merge_copy,
    stray_file_violations,
)

BASE = b"""#include <stdio.h>
#define CAP 16
typedef unsigned char BYTE;
struct S { int a; };
static const int table[] = { 1, 2, 3 };
#ifdef FAST
static int pick(int x) { return x; }
#else
static int pick(int x) { return x + 1; }
#endif
/* swap two ints */
void swap(int *a, int *b) {
    int t = *a;
    *a = *b;
    *b = t;
}

int clamp(int v)
{
    if (v > CAP) { return CAP; }
    return v;
}
"""

SWAP_CONTRACT = (
    b"__CPROVER_requires(__CPROVER_w_ok(a, sizeof(int)))\n"
    b"__CPROVER_assigns(*a, *b)\n"
    b"__CPROVER_ensures(*a == __CPROVER_old(*b))\n"
)


def _rules(base: bytes, snapshot: bytes, function: str = "swap") -> list[int]:
    return [violation.rule for violation in check_edits(base, snapshot, function)]


def _with_swap_contract(source: bytes = BASE, contract: bytes = SWAP_CONTRACT) -> bytes:
    return source.replace(
        b"void swap(int *a, int *b) {", b"void swap(int *a, int *b)\n" + contract + b"{"
    )


def test_an_unmodified_file_breaks_no_rule() -> None:
    assert check_edits(BASE, BASE, "swap") == []


def test_a_contract_in_the_slot_is_allowed_even_when_it_splits_the_signature_line() -> None:
    assert check_edits(BASE, _with_swap_contract(), "swap") == []


def test_a_contract_on_another_function_and_comments_and_whitespace_are_allowed() -> None:
    snapshot = _with_swap_contract().replace(
        b"int clamp(int v)\n{",
        b"int clamp(int v)\n/* never above CAP */\n__CPROVER_ensures(__CPROVER_return_value <= CAP)\n{",
    )
    snapshot = snapshot.replace(b"    int t = *a;", b"\tint   t = *a;  // temporary")
    assert check_edits(BASE, snapshot, "swap") == []


def test_prefixed_helpers_macros_and_types_at_file_scope_are_allowed() -> None:
    helpers = (
        b"#define AVOCADO_SPEC_MAX 8\n"
        b"typedef struct { int lo; int hi; } avocado_spec_range_t;\n"
        b"struct avocado_spec_pair { int a; int b; };\n"
        b"static int avocado_spec_limit = 4;\n"
        b"static int avocado_spec_sorted(const int *p, int n);\n"
        b"static int avocado_spec_same(const int *p, const int *q)\n"
        b"{ return __CPROVER_forall { int k; (0 <= k && k < AVOCADO_SPEC_MAX) ==> p[k] == q[k] }; }\n"
    )
    snapshot = _with_swap_contract().replace(b"/* swap two ints */", helpers)
    assert check_edits(BASE, snapshot, "swap") == []


def test_a_new_conditional_block_around_new_items_is_allowed() -> None:
    block = b"#if __BYTE_ORDER__ == __ORDER_LITTLE_ENDIAN__\n#define AVOCADO_SPEC_LE 1\n#else\n#define AVOCADO_SPEC_LE 0\n#endif\n"
    snapshot = BASE.replace(b"/* swap two ints */", block)
    assert check_edits(BASE, snapshot, "swap") == []


def test_rule_1_a_changed_body_is_reported_with_its_line() -> None:
    snapshot = BASE.replace(b"*a = *b;", b"*a = *a;")
    violations = check_edits(BASE, snapshot, "swap")
    assert [violation.rule for violation in violations] == [1]
    assert violations[0].line == 14 and "function swap changed" in violations[0].message


def test_rule_1_changed_macro_signature_and_removed_or_moved_items() -> None:
    assert _rules(BASE, BASE.replace(b"#define CAP 16", b"#define CAP 32")) == [1]
    assert _rules(
        BASE, BASE.replace(b"void swap(int *a, int *b)", b"void swap(int *a, long *b)")
    ) == [1]
    assert _rules(BASE, BASE.replace(b"typedef unsigned char BYTE;\n", b"")) == [1]
    moved = BASE.replace(b"struct S { int a; };\n", b"").replace(
        b"/* swap", b"struct S { int a; };\n/* swap"
    )
    violations = check_edits(BASE, moved, "swap")
    assert [violation.rule for violation in violations] == [1] and "moved" in violations[0].message


def test_rule_1_distinguishes_operators_that_differ_only_in_spacing() -> None:
    base = b"int f(int a, int b) { return a - -b; }\n"
    assert _rules(base, base.replace(b"a - -b", b"a--b"), "f") == [1]
    assert _rules(base, base.replace(b"a - -b", b"a  -  -b"), "f") == []


def test_rule_2_text_inserted_in_a_body_and_clauses_in_a_body() -> None:
    statement = BASE.replace(
        b"    int t = *a;", b'    __CPROVER_assert(a != b, "distinct");\n    int t = *a;'
    )
    violations = check_edits(BASE, statement, "swap")
    assert [violation.rule for violation in violations] == [2]
    assert violations[0].line == 13 and "inserted inside function swap" in violations[0].message
    clause = BASE.replace(b"    int t = *a;", b"    __CPROVER_requires(a != b)\n    int t = *a;")
    assert _rules(BASE, clause) == [2]


def test_rule_2_a_macro_definition_in_the_contract_slot() -> None:
    snapshot = BASE.replace(
        b"int clamp(int v)\n{", b"int clamp(int v)\n#define AVOCADO_SPEC_X 1\n{"
    )
    assert _rules(BASE, snapshot) == [2]


def test_rule_3_the_target_is_renamed_or_duplicated() -> None:
    renamed = BASE.replace(b"void swap(", b"void renamed_swap(")
    assert set(_rules(BASE, renamed)) == {1, 3, 4}
    duplicated = BASE + b"void swap(int *a, int *b) { }\n"
    assert 3 in _rules(BASE, duplicated)


def test_rule_4_unprefixed_names_and_library_models() -> None:
    for addition in (
        b"static int helper(int x) { return x; }\n",
        b"#define LIMIT 8\n",
        b"void* malloc(unsigned long s) { return __CPROVER_allocate(s, 0); }\n",
        b"static int counter;\n",
        b"typedef int word_t;\n",
    ):
        violations = check_edits(BASE, BASE.replace(b"/* swap two ints */", addition), "swap")
        assert [violation.rule for violation in violations] == [4], addition
        assert violations[0].line == 11, addition


def test_rule_4_new_undef_pragma_and_include() -> None:
    for directive in (
        b"#undef CAP\n",
        b'#pragma CPROVER check disable "pointer"\n',
        b"#include <stdlib.h>\n",
    ):
        snapshot = BASE.replace(b"/* swap two ints */", directive)
        assert _rules(BASE, snapshot) == [4], directive
    in_helper = b'static int avocado_spec_h(int x)\n{\n#pragma CPROVER check disable "bounds"\n return x; }\n'
    assert _rules(BASE, BASE.replace(b"/* swap two ints */", in_helper)) == [4]


def test_rule_4_a_new_conditional_may_not_enclose_or_extend_original_code() -> None:
    enclosing = BASE.replace(b"/* swap two ints */", b"#ifndef AVOCADO_SPEC_OFF").replace(
        b"int clamp(int v)", b"#endif\nint clamp(int v)"
    )
    violations = check_edits(BASE, enclosing, "swap")
    assert [violation.rule for violation in violations] == [4]
    assert "encloses original code" in violations[0].message
    extending = BASE.replace(b"#endif\n", b"#elif defined(AVOCADO_SPEC_X)\n#endif\n")
    assert _rules(BASE, extending) == [4]


def test_rule_4_a_prefixed_name_that_already_exists() -> None:
    base = BASE.replace(b"/* swap two ints */", b"#define AVOCADO_SPEC_N 4")
    snapshot = base.replace(b"void swap(", b"#define AVOCADO_SPEC_N 4\nvoid swap(")
    violations = check_edits(base, snapshot, "swap")
    assert [violation.rule for violation in violations] == [4]
    assert "already exists" in violations[0].message


def test_rule_5_assume_in_a_new_helper_or_macro() -> None:
    helper = b"static int avocado_spec_bind(int *p) { __CPROVER_assume(p != 0); return 1; }\n"
    assert _rules(BASE, BASE.replace(b"/* swap two ints */", helper)) == [5]
    macro = b"#define AVOCADO_SPEC_BIND(p) __CPROVER_assume((p) != 0)\n"
    assert _rules(BASE, BASE.replace(b"/* swap two ints */", macro)) == [5]


def test_rule_8_literally_false_preconditions_only() -> None:
    for argument in (b"0", b"false", b"( (0) )", b"1 == 0", b"/* never */ 0"):
        snapshot = _with_swap_contract(contract=b"__CPROVER_requires(" + argument + b")\n")
        assert _rules(BASE, snapshot) == [8], argument
    assert _rules(BASE, _with_swap_contract(contract=b"__CPROVER_requires(a != 0)\n")) == []
    # A false precondition that was already in the file is not this session's doing.
    base = _with_swap_contract(contract=b"__CPROVER_requires(0)\n")
    assert check_edits(base, base, "swap") == []


def test_violations_render_with_rule_number_title_and_line() -> None:
    snapshot = BASE.replace(b"/* swap two ints */", b"#undef CAP")
    rendered = [str(violation) for violation in check_edits(BASE, snapshot, "swap")]
    assert rendered == [
        "rule 4 (new names carry the reserved prefix): line 11: new `#undef` is not allowed: "
        "it can change which branch of an existing #if is compiled"
    ]


def test_stray_files_become_rule_6_violations() -> None:
    violations = stray_file_violations(["added:scratch.c", "modified:q.h"])
    assert [violation.rule for violation in violations] == [6, 6]
    assert "scratch.c was added" in violations[0].message
    assert "q.h was modified" in violations[1].message


def test_quicksort_with_quantified_contracts_matches_its_contract_free_form() -> None:
    from tools.util.cbmc_clause_stripper import strip_cbmc_clauses  # noqa: PLC0415

    annotated = Path("test/data/quicksort_with_forall.c").read_bytes()
    bare, _ = strip_cbmc_clauses(annotated)
    assert check_edits(bare, annotated, "partition") == []


CLAMP_CONTRACT = b"__CPROVER_ensures(__CPROVER_return_value <= CAP)\n"


def _with_clamp_contract(source: bytes) -> bytes:
    return source.replace(b"int clamp(int v)\n{", b"int clamp(int v)\n" + CLAMP_CONTRACT + b"{")


def _merge(snapshot: bytes, frozen=None, canonical: bytes = BASE):
    return merge_copy(
        canonical=canonical,
        snapshot=snapshot,
        fork_base=BASE,
        function="swap",
        frozen_functions=frozen,
    )


def test_merge_of_a_clean_copy_is_the_copy_itself() -> None:
    helper = b"#define AVOCADO_SPEC_MAX 8\nstatic int avocado_spec_ok(int x) { return x < AVOCADO_SPEC_MAX; }\n"
    snapshot = _with_swap_contract().replace(b"/* swap two ints */", helper)
    merged, report = _merge(snapshot)
    assert report.merged and SWAP_CONTRACT in merged and helper in merged
    # The merge is built on the canonical file, so its comments survive the session.
    assert b"/* swap two ints */" in merged and check_edits(BASE, merged, "swap") == []
    assert report.transplanted == ["macro:AVOCADO_SPEC_MAX", "function:avocado_spec_ok"]
    assert report.carried == [] and report.dropped == []


def test_merge_reverts_carries_or_freezes_a_contract_on_another_function() -> None:
    snapshot = _with_clamp_contract(_with_swap_contract())
    merged, report = _merge(snapshot)
    assert report.merged and report.carried == [] and report.dropped == ["function:clamp"]
    assert merged == _with_swap_contract()
    merged, report = _merge(snapshot, frozen={"swap"})
    assert report.carried == ["clamp"] and report.dropped == [] and merged == snapshot
    merged, report = _merge(snapshot, frozen={"swap", "clamp"})
    assert report.carried == []
    assert report.dropped == ["function:clamp (a live session is working on it)"]
    assert SWAP_CONTRACT in merged and CLAMP_CONTRACT not in merged


def test_merge_refuses_a_copy_that_changes_code() -> None:
    merged, report = _merge(BASE.replace(b"*a = *b;", b"*a = *a;"))
    assert not report.merged and merged == BASE
    assert len(report.violations) == 1 and report.violations[0].startswith("rule 1 ")


def test_merge_does_not_need_the_file_to_parse_as_c() -> None:
    # A quantifier in a helper's body, and an attribute macro before a return type, are what made
    # the tree-sitter based locator lose every function that follows them.
    hostile = (
        b"static int avocado_spec_all(const int *p)\n"
        b"{ return __CPROVER_forall { int k; (0 <= k && k < 4) ==> p[k] == 0 }; }\n"
    )
    base = BASE.replace(b"/* swap two ints */", hostile).replace(
        b"int clamp(", b"FORCE_INLINE int clamp("
    )
    snapshot = _with_swap_contract(base).replace(
        b"FORCE_INLINE int clamp(int v)\n{",
        b"FORCE_INLINE int clamp(int v)\n" + CLAMP_CONTRACT + b"{",
    )
    assert check_edits(base, snapshot, "swap") == []
    merged, report = merge_copy(
        canonical=base,
        snapshot=snapshot,
        fork_base=base,
        function="swap",
        frozen_functions={"swap"},
    )
    assert report.merged and merged == snapshot and report.carried == ["clamp"]


def test_merge_onto_a_canonical_file_that_changed_since_the_fork() -> None:
    canonical = BASE.replace(b"/* swap two ints */", b"#define AVOCADO_SPEC_OTHER 1")
    merged, report = _merge(_with_swap_contract(), canonical=canonical)
    assert report.merged, report.reason
    assert SWAP_CONTRACT in merged and b"#define AVOCADO_SPEC_OTHER 1" in merged


def test_declarations_inside_an_extern_c_block_are_separate_items() -> None:
    base = (
        b'#if defined (__cplusplus)\nextern "C" {\n#endif\n'
        b"int first(int a);\nint second(int a) { return a; }\n"
        b"#if defined (__cplusplus)\n}\n#endif\n"
    )
    assert function_line(base, "second") == 5
    snapshot = base.replace(
        b"int second(int a) {", b"int second(int a)\n__CPROVER_ensures(1 == 1)\n{"
    )
    assert check_edits(base, snapshot, "second") == []
    assert _rules(base, base.replace(b"return a;", b"return a + 1;"), "second") == [2]


SWAP_HELPER = b"#define AVOCADO_SPEC_SWAP_N 2\n"
CLAMP_HELPER = b"static int avocado_spec_in_range(int v) { return v <= CAP; }\n"


def _swap_session_copy() -> bytes:
    return _with_swap_contract().replace(
        b"/* swap two ints */", b"/* swap two ints */\n" + SWAP_HELPER
    )


def _clamp_session_copy(contract: bytes = CLAMP_CONTRACT) -> bytes:
    return BASE.replace(
        b"int clamp(int v)\n{", CLAMP_HELPER + b"int clamp(int v)\n" + contract + b"{"
    )


def test_two_sessions_forked_from_the_same_base_both_merge() -> None:
    after_swap, first = merge_copy(
        canonical=BASE, snapshot=_swap_session_copy(), fork_base=BASE, function="swap"
    )
    assert first.merged
    merged, second = merge_copy(
        canonical=after_swap, snapshot=_clamp_session_copy(), fork_base=BASE, function="clamp"
    )
    assert second.merged, second.reason
    assert second.transplanted == ["function:avocado_spec_in_range"]
    for piece in (SWAP_CONTRACT, SWAP_HELPER, CLAMP_CONTRACT, CLAMP_HELPER):
        assert piece in merged
    # Each helper still precedes the function whose contract uses it, and no code changed.
    assert merged.index(SWAP_HELPER) < merged.index(b"void swap(")
    assert merged.index(CLAMP_HELPER) < merged.index(b"int clamp(")
    assert check_edits(BASE, merged, "clamp") == []
    # The original comment before the swap session's helper is not copied along with it.
    assert after_swap.count(b"/* swap two ints */") == 1
    assert merged.count(b"/* swap two ints */") == 1


def test_an_edit_to_a_contract_that_someone_else_changed_meanwhile_is_left_out() -> None:
    theirs = _clamp_session_copy(
        contract=b"__CPROVER_ensures(__CPROVER_return_value >= 0 || v < 0)\n"
    )
    canonical, _ = merge_copy(canonical=BASE, snapshot=theirs, fork_base=BASE, function="clamp")
    mine = _with_clamp_contract(_with_swap_contract())
    merged, report = merge_copy(
        canonical=canonical, snapshot=mine, fork_base=BASE, function="swap", frozen_functions=set()
    )
    assert report.merged and report.carried == []
    assert report.dropped == ["function:clamp (changed in the canonical file since the fork)"]
    assert SWAP_CONTRACT in merged and CLAMP_CONTRACT not in merged
    assert b"__CPROVER_return_value >= 0 || v < 0" in merged


def test_the_same_edit_made_by_two_sessions_is_not_a_conflict() -> None:
    canonical, _ = merge_copy(
        canonical=BASE, snapshot=_with_clamp_contract(BASE), fork_base=BASE, function="clamp"
    )
    merged, report = merge_copy(
        canonical=canonical,
        snapshot=_with_clamp_contract(_with_swap_contract()),
        fork_base=BASE,
        function="swap",
        frozen_functions=set(),
    )
    assert report.merged and report.dropped == [] and report.carried == []
    assert merged.count(CLAMP_CONTRACT) == 1 and SWAP_CONTRACT in merged


def test_a_new_name_two_sessions_both_add() -> None:
    canonical, _ = merge_copy(
        canonical=BASE, snapshot=_swap_session_copy(), fork_base=BASE, function="swap"
    )
    same = BASE.replace(
        b"int clamp(int v)\n{", SWAP_HELPER + b"int clamp(int v)\n" + CLAMP_CONTRACT + b"{"
    )
    merged, report = merge_copy(
        canonical=canonical, snapshot=same, fork_base=BASE, function="clamp"
    )
    assert report.merged and report.skipped_duplicates == ["macro:AVOCADO_SPEC_SWAP_N"]
    assert merged.count(SWAP_HELPER) == 1
    different = same.replace(b"AVOCADO_SPEC_SWAP_N 2", b"AVOCADO_SPEC_SWAP_N 3")
    merged, report = merge_copy(
        canonical=canonical, snapshot=different, fork_base=BASE, function="clamp"
    )
    assert not report.merged and merged == canonical
    assert "macro:AVOCADO_SPEC_SWAP_N is already defined differently" in report.reason


def test_frozen_functions_are_the_other_live_sessions_and_not_their_callees() -> None:
    assert frozen_functions_for("partition", {"partition"}) == set()
    assert frozen_functions_for("other", {"other", "partition"}) == {"partition"}
    # quickSort calls partition and swap, and neither is frozen on its account.
    assert frozen_functions_for("partition", {"partition", "quickSort"}) == {"quickSort"}
    assert frozen_functions_for("swap", {"swap", "quickSort"}) == {"quickSort"}


def test_merge_reports_the_contracts_other_sessions_changed_since_the_fork() -> None:
    canonical = _with_clamp_contract(BASE)
    # The session never touched clamp: the canonical contract stands, and is reported as new.
    merged, report = _merge(_with_swap_contract(), frozen=set(), canonical=canonical)
    assert report.merged and report.dropped == [] and report.changed_since_fork == ["clamp"]
    assert SWAP_CONTRACT in merged and CLAMP_CONTRACT in merged
    assert report.to_record()["changed_since_fork"] == ["clamp"]
    # A copy that already holds the new contract merges the same way.
    merged, report = _merge(
        _with_clamp_contract(_with_swap_contract()), frozen=set(), canonical=canonical
    )
    assert report.merged and report.dropped == [] and report.changed_since_fork == ["clamp"]
    _merged, report = _merge(_with_swap_contract(), frozen=set())
    assert report.changed_since_fork == []


def test_functions_with_changed_contracts_ignores_comments_helpers_and_whitespace() -> None:
    assert functions_with_changed_contracts(BASE, BASE) == []
    helper = b"#define AVOCADO_SPEC_MAX 8\n"
    reformatted = BASE.replace(b"/* swap two ints */", helper).replace(b"int v)\n{", b"int v) {")
    assert functions_with_changed_contracts(BASE, reformatted) == []
    both = _with_clamp_contract(_with_swap_contract())
    assert functions_with_changed_contracts(BASE, both) == ["swap", "clamp"]
    assert functions_with_changed_contracts(both, _with_swap_contract()) == ["clamp"]


def test_function_header_is_the_signature_and_the_contract() -> None:
    assert function_header(BASE, "clamp") == b"int clamp(int v)"
    header = function_header(_with_clamp_contract(BASE), "clamp")
    assert header == b"int clamp(int v)\n" + CLAMP_CONTRACT.rstrip()
    assert function_header(BASE, "missing") is None


# `struct`, `union` and `enum` in a parameter list or a return type, as in kilo's `abAppend`.
STRUCT_PARAMETERS = b"""struct abuf { char *b; int len; };
typedef struct erow { int size; } erow;
void abFree(struct abuf *ab) {
    ab->len = 0;
}
struct abuf *abNew(union u *x, enum e k) {
    return 0;
}
int after(int v) {
    return v;
}
"""


def test_a_struct_parameter_does_not_hide_the_function_or_the_ones_after_it() -> None:
    for function in ("abFree", "abNew", "after"):
        assert len(function_body_offsets(STRUCT_PARAMETERS, function)) == 1
    # The type definitions are still declarations, not functions.
    assert function_body_offsets(STRUCT_PARAMETERS, "abuf") == []
    assert function_body_offsets(STRUCT_PARAMETERS, "erow") == []


def test_contracts_on_functions_with_or_after_a_struct_parameter_are_merged() -> None:
    contract = (
        b"\n__CPROVER_requires(__CPROVER_is_fresh(ab, sizeof(struct abuf)))\n__CPROVER_assigns()\n"
    )
    for function, signature in (
        ("abFree", b"void abFree(struct abuf *ab)"),
        ("abNew", b"struct abuf *abNew(union u *x, enum e k)"),
        ("after", b"int after(int v)"),
    ):
        snapshot = STRUCT_PARAMETERS.replace(signature + b" {", signature + contract + b"{")
        assert check_edits(STRUCT_PARAMETERS, snapshot, function) == []
        assert functions_with_changed_contracts(STRUCT_PARAMETERS, snapshot) == [function]
        merged, report = merge_copy(
            canonical=STRUCT_PARAMETERS,
            snapshot=snapshot,
            fork_base=STRUCT_PARAMETERS,
            function=function,
            frozen_functions=set(),
        )
        assert report.merged and report.dropped == [] and merged == snapshot


def _parser_without_the_struct_parameter_fix(prefix) -> bool:
    """`_opens_function_body` as it was before the fix: a `struct` parameter hides the body."""
    texts = [token.text for token in prefix if not token.is_directive]
    if "=" in texts:
        return False
    last_aggregate = max(
        (index for index, text in enumerate(texts) if text in {"struct", "union", "enum"}),
        default=-1,
    )
    return "(" in texts[last_aggregate + 1 :]


def test_a_session_function_the_parser_cannot_find_fails_the_merge(monkeypatch) -> None:
    # With the old parser, everything from `abFree` on is one unnamed item.
    monkeypatch.setattr(edit_rules, "_opens_function_body", _parser_without_the_struct_parameter_fix)
    snapshot = STRUCT_PARAMETERS.replace(
        b"int after(int v) {", b"int after(int v)\n__CPROVER_ensures(__CPROVER_return_value == v)\n{"
    )
    merged, report = merge_copy(
        canonical=STRUCT_PARAMETERS,
        snapshot=snapshot,
        fork_base=STRUCT_PARAMETERS,
        function="after",
        frozen_functions=set(),
    )
    assert not report.merged and merged == STRUCT_PARAMETERS
    assert "after could not be found as a function definition" in report.reason
    assert report.dropped == []


def test_a_contract_edit_to_an_item_the_merge_cannot_name_fails_the_merge(monkeypatch) -> None:
    # `pick` is defined twice. With the old parser the first definition is found, but the second
    # is in an unnamed item, and its contract would once have been left out without a word.
    monkeypatch.setattr(edit_rules, "_opens_function_body", _parser_without_the_struct_parameter_fix)
    base = (
        b"struct abuf { char *b; int len; };\n"
        b"#ifdef FAST\nint pick(int x) {\n    return x;\n}\n#endif\n"
        b"void abFree(struct abuf *ab) {\n    ab->len = 0;\n}\n"
        b"#ifndef FAST\nint pick(int x) {\n    return x + 1;\n}\n#endif\n"
    )
    snapshot = base.replace(
        b"int pick(int x) {", b"int pick(int x)\n__CPROVER_ensures(__CPROVER_return_value >= x)\n{"
    )
    merged, report = merge_copy(
        canonical=base, snapshot=snapshot, fork_base=base, function="pick", frozen_functions=set()
    )
    assert not report.merged and merged == base
    assert "the merge cannot name" in report.reason and "the contract of pick" in report.reason


CONDITIONAL_DEFINITIONS = b"""#ifdef FAST
static int pick(int x) { return x; }
#else
static int pick(int x) { return x + 1; }
#endif
int pick_twice(int x);
"""


def test_each_definition_of_a_function_is_found() -> None:
    offsets = function_body_offsets(CONDITIONAL_DEFINITIONS, "pick")
    assert len(offsets) == 2
    assert all(CONDITIONAL_DEFINITIONS[offset : offset + 1] == b"{" for offset in offsets)
    # A declaration has no body.
    assert function_body_offsets(CONDITIONAL_DEFINITIONS, "pick_twice") == []
    assert function_body_offsets(CONDITIONAL_DEFINITIONS, "missing") == []


def test_a_function_inside_an_extern_c_block_is_found() -> None:
    source = (
        b'#if defined (__cplusplus)\nextern "C" {\n#endif\n'
        b"int first(int a);\nint second(int a) { return a; }\n"
        b"#if defined (__cplusplus)\n}\n#endif\n"
    )
    assert function_body_offsets(source, "first") == []
    assert len(function_body_offsets(source, "second")) == 1


LOOP = b"""int f(int n) {
    int i;
    for (i = 0; i < n; i++)
    __CPROVER_assigns(i)
    __CPROVER_loop_invariant(0 <= i && i <= n)
    { }
    return i;
}
"""


def test_an_original_clause_in_a_body_is_code() -> None:
    assert check_edits(LOOP, LOOP, "f") == []
    assert _rules(LOOP, LOOP.replace(b"    __CPROVER_assigns(i)\n", b""), "f") == [1]
    assert _rules(LOOP, LOOP.replace(b"__CPROVER_assigns(i)", b"__CPROVER_assigns(n)"), "f") == [1]
    contract = LOOP.replace(b"int f(int n) {", b"int f(int n)\n__CPROVER_requires(n >= 0)\n{")
    assert check_edits(LOOP, contract, "f") == []


def test_rule_8_compares_with_the_preconditions_of_the_same_item() -> None:
    base = b"int g(int x)\n__CPROVER_requires(0)\n{ return x; }\nint f(int x) { return x; }\n"
    snapshot = base.replace(b"int f(int x) {", b"int f(int x)\n__CPROVER_requires(0)\n{")
    violations = check_edits(base, snapshot, "f")
    assert [violation.rule for violation in violations] == [8] and violations[0].line == 5


TAIL = b"int g(int x) { return x; }\n#ifdef X\nvoid f(void) { }\n#endif\n"


def _merge_onto(canonical: bytes, snapshot: bytes, base: bytes = TAIL):
    return merge_copy(canonical=canonical, snapshot=snapshot, fork_base=base, function="f")


def test_a_conditional_block_both_sessions_add_is_skipped_whole() -> None:
    block = b"#ifdef AVOCADO_SPEC_G\nint avocado_spec_a;\n#endif\n"
    merged, report = _merge_onto(TAIL + block, TAIL + block)
    assert report.merged, report.reason
    assert merged == TAIL + block and report.transplanted == []
    assert report.skipped_duplicates == ["ifdef:#ifdef AVOCADO_SPEC_G", "declaration:avocado_spec_a"]


def test_conditional_blocks_with_different_conditions_both_merge() -> None:
    theirs = b"#if defined(AVOCADO_SPEC_A)\nint avocado_spec_a;\n#endif\n"
    mine = b"#if defined(AVOCADO_SPEC_B)\nint avocado_spec_b;\n#endif\n"
    merged, report = _merge_onto(TAIL + theirs, TAIL + mine)
    assert report.merged, report.reason
    assert theirs in merged and mine in merged


def test_an_endif_is_aligned_with_the_endif_of_its_own_block() -> None:
    inner = b"#ifdef AVOCADO_SPEC_Y\nint avocado_spec_y;\n#endif\n"
    canonical = TAIL.replace(b"void f(void) { }\n", b"void f(void) { }\n" + inner)
    assert check_edits(TAIL, canonical, "f") == []
    merged, report = _merge_onto(canonical, TAIL + b"int avocado_spec_z;\n")
    assert report.merged, report.reason
    assert merged == canonical + b"int avocado_spec_z;\n"


def test_a_new_name_two_sessions_add_as_different_kinds_is_a_conflict() -> None:
    theirs = b"int avocado_spec_a(int x) { return x; }\n"
    merged, report = _merge_onto(TAIL + theirs, TAIL + b"int avocado_spec_a;\n")
    assert not report.merged and merged == TAIL + theirs
    assert "declaration:avocado_spec_a is already defined differently" in report.reason


def test_an_original_comment_before_added_items_is_not_copied() -> None:
    base = b"/* License */\nint g(int x) { return x; }\nvoid f(void) { }\n"
    canonical = base + b"int avocado_spec_other;\n"
    snapshot = base.replace(b"/* License */\n", b"/* License */\nint avocado_spec_q;\n")
    merged, report = _merge_onto(canonical, snapshot, base)
    assert report.merged, report.reason
    assert merged == snapshot + b"int avocado_spec_other;\n"


SHORT = b"int a;\nint f(int x) { return x; }\n"
HELPER_H = b"int avocado_spec_h(int x) { return x; }\n"
HELPER_H2 = b"int avocado_spec_h2(int x) { return avocado_spec_h(x); }\n"


def test_added_items_go_after_those_other_sessions_added_at_the_same_place() -> None:
    canonical = SHORT.replace(b"int a;\n", b"int a;\n" + HELPER_H)
    snapshot = SHORT.replace(b"int a;\n", b"int a;\n" + HELPER_H + HELPER_H2)
    merged, report = _merge_onto(canonical, snapshot, SHORT)
    assert report.merged, report.reason
    assert report.skipped_duplicates == ["function:avocado_spec_h"]
    assert merged == snapshot


def test_a_contract_edit_after_a_comment_marker_in_a_string_is_merged() -> None:
    base = b'int f(const char *s)\n__CPROVER_requires(s[0] == "//"[0] && 1)\n{ return 0; }\n'
    snapshot = base.replace(b"&& 1", b"&& s != 0")
    merged, report = _merge_onto(base, snapshot, base)
    assert report.merged and merged == snapshot


def test_the_sessions_own_function_keeps_the_contract_of_the_copy() -> None:
    canonical = SHORT.replace(b"int f(int x) {", b"int f(int x)\n__CPROVER_requires(x > 0)\n{")
    merged, report = _merge_onto(canonical, SHORT, SHORT)
    assert report.merged and merged == SHORT and report.changed_since_fork == ["f"]


def test_rule_8_a_stray_false_precondition_does_not_excuse_one_in_a_new_helper() -> None:
    base = SHORT.replace(b"int a;\n", b"int a;\n__CPROVER_requires(0)\n")
    helper = b"int avocado_spec_h(int x)\n__CPROVER_requires(0)\n{ return x; }\n"
    assert check_edits(base, base, "f") == []
    assert _rules(base, base + helper, "f") == [8]


def test_rule_5_assume_in_a_contract_clause() -> None:
    snapshot = SHORT.replace(
        b"int f(int x) {", b"int f(int x)\n__CPROVER_requires((__CPROVER_assume(x > 0), 1))\n{"
    )
    assert _rules(SHORT, snapshot, "f") == [5]


F_CONTRACT = b"int f(int x)\n__CPROVER_requires(x > 0)\n{"


def test_an_apostrophe_in_a_directive_does_not_hide_later_contract_clauses() -> None:
    base = b"#ifdef X\n#error don't\n#endif\nint f(int x) { return x; }\n"
    assert check_edits(base, base.replace(b"int f(int x) {", F_CONTRACT), "f") == []


def test_a_comment_marker_in_a_string_in_a_directive_does_not_start_a_comment() -> None:
    snapshot = SHORT.replace(b"int f(int x) {", b'#define AVOCADO_SPEC_S "/*"\n' + F_CONTRACT)
    assert check_edits(SHORT, snapshot, "f") == []


def test_a_duplicate_that_the_canonical_file_has_later_is_moved_before_its_use() -> None:
    canonical = SHORT + HELPER_H
    snapshot = SHORT.replace(b"int a;\n", b"int a;\n" + HELPER_H + HELPER_H2)
    merged, report = _merge_onto(canonical, snapshot, SHORT)
    assert report.merged, report.reason
    assert report.skipped_duplicates == ["function:avocado_spec_h (moved before its first use)"]
    assert merged == snapshot


def test_a_comment_the_session_wrote_before_an_original_comment_is_kept() -> None:
    base = b"int a;\n/* doc of f */\nint f(int x) { return x; }\n"
    snapshot = base.replace(b"/* doc of f */\n", b"/* my helper */\n/* doc of f */\n" + HELPER_H)
    merged, report = _merge_onto(base, snapshot, base)
    assert report.merged, report.reason
    assert merged.count(b"/* my helper */") == 1 and merged.count(b"/* doc of f */") == 1
    assert merged.index(b"/* my helper */") < merged.index(HELPER_H)


def test_a_definition_of_a_helper_another_session_declared_merges() -> None:
    prototype = b"int avocado_spec_h(int x);\n"
    canonical = SHORT.replace(b"int a;\n", b"int a;\n" + prototype)
    snapshot = SHORT.replace(b"int a;\n", b"int a;\n" + prototype + HELPER_H)
    merged, report = _merge_onto(canonical, snapshot, SHORT)
    assert report.merged, report.reason
    assert merged == snapshot


def test_a_loop_contract_in_a_new_helper_is_allowed() -> None:
    helper = (
        b"int avocado_spec_count(int n) {\n    int i = 0;\n    while (i < n)\n"
        b"    __CPROVER_assigns(i)\n    { i++; }\n    return i;\n}\n"
    )
    assert check_edits(SHORT, SHORT.replace(b"int f(", helper + b"int f("), "f") == []


def test_an_attribute_in_a_new_struct_definition() -> None:
    struct = b"struct __attribute__((packed)) avocado_spec_s { int a; };\n"
    assert check_edits(SHORT, SHORT.replace(b"int f(", struct + b"int f("), "f") == []
