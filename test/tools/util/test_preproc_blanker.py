"""Tests for blanking preprocessor conditionals before tree-sitter parsing."""

from pathlib import Path

from tools.util.preproc_blanker import blank_preprocessor_conditionals


def _newline_offsets(data: bytes) -> list[int]:
    return [i for i, b in enumerate(data) if b == ord("\n")]


def _assert_invariants(source: bytes, blanked: bytes) -> None:
    assert len(blanked) == len(source), "Blanking must preserve the byte length"
    assert _newline_offsets(blanked) == _newline_offsets(source), (
        "Blanking must preserve newline offsets"
    )


def test_in_body_conditional_directives_are_blanked() -> None:
    source = b"int f(int x)\n{\n#if FAST\nlbl:\n#endif\n    return x;\n}\n"
    blanked = blank_preprocessor_conditionals(source)
    _assert_invariants(source, blanked)
    assert blanked == b"int f(int x)\n{\n        \nlbl:\n      \n    return x;\n}\n", (
        f"Expected only the in-body directive lines to be blanked, got {blanked!r}"
    )


def test_top_level_conditional_directives_are_kept() -> None:
    source = b"#if A\nint f(void) { return 1; }\n#else\nint f(void) { return 2; }\n#endif\n"
    blanked = blank_preprocessor_conditionals(source)
    _assert_invariants(source, blanked)
    assert blanked == source, "Top-level conditionals must be left untouched"


def test_if_zero_branch_is_blanked_up_to_else() -> None:
    source = b"int f(void)\n{\n#if 0\n    dead();\n#else\n    live();\n#endif\n    return 0;\n}\n"
    blanked = blank_preprocessor_conditionals(source)
    _assert_invariants(source, blanked)
    assert b"dead" not in blanked, f"Expected the #if 0 branch to be blanked, got {blanked!r}"
    assert b"live();" in blanked, f"Expected the #else branch to be kept, got {blanked!r}"
    assert b"#" not in blanked, f"Expected in-body directives to be blanked, got {blanked!r}"


def test_if_zero_at_top_level_keeps_directive_lines() -> None:
    source = b"#if 0\nint dead(void) { return 0; }\n#endif\nint live(void) { return 1; }\n"
    blanked = blank_preprocessor_conditionals(source)
    _assert_invariants(source, blanked)
    assert blanked == b"#if 0\n                            \n#endif\nint live(void) { return 1; }\n", (
        f"Expected the #if 0 contents blanked and directives kept, got {blanked!r}"
    )


def test_if_zero_handles_nested_conditionals() -> None:
    source = (
        b"#if 0\n#ifdef X\nint a(void) { return 0; }\n#endif\nint b(void) { return 0; }\n#endif\n"
        b"int c(void) { return 0; }\n"
    )
    blanked = blank_preprocessor_conditionals(source)
    _assert_invariants(source, blanked)
    assert b"int a" not in blanked and b"int b" not in blanked, (
        f"Expected the whole nested #if 0 group to be blanked, got {blanked!r}"
    )
    assert b"int c(void)" in blanked, f"Expected code after the group to survive, got {blanked!r}"


def test_if_zero_variants_are_recognised() -> None:
    for head in (b"#if 0", b"# if 0", b"#if (0)", b"#if 0 /* off */", b"#if 0 // off"):
        source = head + b"\ndead();\n#endif\n"
        blanked = blank_preprocessor_conditionals(source)
        _assert_invariants(source, blanked)
        assert b"dead" not in blanked, f"Expected {head!r} to start a dead branch, got {blanked!r}"


def test_non_zero_conditions_are_not_treated_as_dead() -> None:
    for head in (b"#if 1", b"#if 00 + 1", b"#if X", b"#ifdef 0", b"#if 0x1"):
        source = head + b"\nkeep();\n#endif\n"
        blanked = blank_preprocessor_conditionals(source)
        _assert_invariants(source, blanked)
        assert b"keep();" in blanked, f"Expected {head!r} to keep its contents, got {blanked!r}"


def test_directives_in_comments_and_strings_are_ignored() -> None:
    source = (
        b"int f(void)\n{\n    /* comment\n#if 0\n    */\n    const char* s = \"#if 0\";\n"
        b"    return s[0];\n}\n"
    )
    blanked = blank_preprocessor_conditionals(source)
    _assert_invariants(source, blanked)
    assert blanked == source, "Directive-looking text in comments/strings must be ignored"


def test_braces_in_other_directives_do_not_count() -> None:
    source = b"#define OPEN {\n#if A\nint f(void) { return 0; }\n#endif\n"
    blanked = blank_preprocessor_conditionals(source)
    _assert_invariants(source, blanked)
    assert blanked == source, "A brace in a #define must not make the next #if look in-body"


def test_invariants_hold_on_fixtures() -> None:
    for path in sorted(Path("test/data").glob("*.c")):
        source = path.read_bytes()
        _assert_invariants(source, blank_preprocessor_conditionals(source))
