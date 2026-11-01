"""Tests for `tools.util.edit_rules`: splitting a C file into top-level items."""

from tools.util.edit_rules import _split_items, _tokenize, function_body_offsets

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


def _names(source: bytes) -> list[str | None]:
    return [item.name for item in _split_items(_tokenize(source))]


def test_a_comment_opener_in_a_directive_string_does_not_hide_the_functions_after_it() -> None:
    source = b'#define GLOB "/*"\nint f(void) { return 1; }\nint g(void) { return 2; }\n/* x */\n'
    assert len(function_body_offsets(source, "f")) == 1
    assert len(function_body_offsets(source, "g")) == 1


def test_an_apostrophe_in_a_directive_does_not_hide_the_functions_after_it() -> None:
    source = b"#error don't\nint f(void) { return 1; }\nint g(void) { return '2'; }\n"
    assert len(function_body_offsets(source, "f")) == 1
    assert len(function_body_offsets(source, "g")) == 1


def test_a_header_that_differs_between_branches_has_one_body_offset_per_header() -> None:
    source = (
        b"#ifdef X\nvoid f(int a) {\n#else\nvoid f(int a, int b) {\n#endif\n  return;\n}\n"
        b"int g(void) { return 0; }\n"
    )
    offsets = function_body_offsets(source, "f")
    assert len(offsets) == 2
    assert all(source[offset : offset + 1] == b"{" for offset in offsets)
    assert len(function_body_offsets(source, "g")) == 1


def test_braces_opened_in_each_branch_of_a_conditional_inside_a_body_count_once() -> None:
    source = (
        b"int f(int a) {\n#ifdef X\n  if (a) {\n#else\n  if (!a) {\n#endif\n"
        b"    a++;\n  }\n  return a;\n}\nint g(void) { return 0; }\n"
    )
    assert len(function_body_offsets(source, "f")) == 1
    assert len(function_body_offsets(source, "g")) == 1


def test_a_prototype_is_not_named_after_a_function_pointer_parameter() -> None:
    assert _names(b"void sort(int *a, int (*cmp)(int, int));") == ["sort"]
    assert _names(b"int (*handler)(int);") == ["handler"]
    assert _names(b"typedef int (*cmp_t)(int, int);") == ["cmp_t"]


def test_an_attribute_after_the_declarator_is_not_the_name() -> None:
    assert _names(b"int x __attribute__((aligned(4)));") == ["x"]
    assert _names(b"int f(void) __attribute__((pure));") == ["f"]
    assert _names(b'int y asm("real_y");') == ["y"]
    assert _names(b"__attribute__((unused)) static int z;") == ["z"]


def test_a_struct_defined_in_the_return_type_does_not_hide_the_function_name() -> None:
    source = b"struct S { int a; } f(void) { struct S s = {0}; return s; }\n"
    offsets = function_body_offsets(source, "f")
    assert offsets == [source.index(b"{", source.index(b"f(void)"))]


def test_a_brace_opened_and_closed_in_separate_conditionals_counts_once() -> None:
    source = (
        b"int f(int a) {\n#ifdef X\n  if (a) {\n#else\n#endif\n  a++;\n"
        b"#ifdef X\n  }\n#endif\n  return a;\n}\nint g(void) { return 0; }\n"
    )
    assert _names(source)[-2:] == ["f", "g"]
    assert len(function_body_offsets(source, "g")) == 1


def test_comment_delimiters_and_spaces_inside_a_directive_string_are_kept() -> None:
    def directive(source: bytes) -> tuple[str, ...]:
        return _split_items(_tokenize(source))[0].tokens

    assert directive(b'#define U "http://a.com"\n') == ('#define U "http://a.com"',)
    assert directive(b'#define U "http://a.com"\n') != directive(b'#define U "http://b.com"\n')
    assert directive(b'#define S "a  b"\n') != directive(b'#define S "a b"\n')
    assert directive(b"#  define  F(x)   /* c */ x\n") == ("#define F(x) x",)
    assert directive(b"#define F(x) x\n") != directive(b"#define F (x) x\n")


def test_a_multi_line_contract_clause_does_not_end_a_macro_early() -> None:
    source = (
        b"#define C(a) __CPROVER_requires(a > 0 && \\\n a < 9) \\\n"
        b"  int helper(void) { return a; }\n"
        b"int f(void) { return 0; }\n"
    )
    assert _names(source) == ["C", "f"]
    assert function_body_offsets(source, "helper") == []
