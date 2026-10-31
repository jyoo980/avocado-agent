"""Tests for `tools.util.edit_rules`: splitting a C file into top-level items."""

from tools.util.edit_rules import function_body_offsets

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
