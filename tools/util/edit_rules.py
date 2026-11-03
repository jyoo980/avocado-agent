"""Split a C file into top-level items without parsing it, and locate function bodies.

Contract clauses and comments are erased, the file is split into tokens and then into top-level
items by brace depth alone. That is robust to constructs the C grammar cannot parse
(`__CPROVER_forall { ... }` in a helper, attribute macros, operator macros).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from tools.util.cbmc_clause_stripper import strip_cbmc_clauses
from tools.util.preproc_blanker import logical_line_end

if TYPE_CHECKING:
    from collections.abc import Sequence

_TOKEN = re.compile(
    r"""
      (?P<space>[ \t\r\f\v]+|\\\n)
    | (?P<newline>\n)
    | (?P<comment>/\*.*?\*/|//(?:\\.|[^\\\n])*)
    | (?P<literal>"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*')
    | (?P<word>[A-Za-z_0-9]+)
    | (?P<operator>>>=|<<=|\.\.\.|->|\+\+|--|<<|>>|<=|>=|==|!=|&&|\|\||[-+*/%&^|]=|\#\#|.)
    """,
    re.VERBOSE | re.DOTALL,
)
_DIRECTIVE_HEAD = re.compile(r"#\s*(\w*)\s*(\w*)")
# Matches a normalized `#if 0` or `#if (0)`, whose first branch is dead code.
_DEAD_CONDITIONAL = re.compile(r"#if\s*\(*\s*0\s*\)*")
_IDENTIFIER = re.compile(r"[A-Za-z_]\w*\Z")
_CONDITIONAL_OPENERS = frozenset({"if", "ifdef", "ifndef"})
_CONDITIONAL_CONTINUATIONS = frozenset({"elif", "elifdef", "elifndef", "else", "endif"})
# The directives whose second word is a name they introduce or remove.
_NAMING_DIRECTIVES = frozenset({"define", "undef"})
_AGGREGATE_KEYWORDS = frozenset({"struct", "union", "enum"})
# Keywords that take a parenthesized argument and can follow a declarator.
_ATTRIBUTE_KEYWORDS = frozenset(
    {"__attribute__", "__attribute", "__declspec", "asm", "__asm", "__asm__"}
)


@dataclass(frozen=True)
class _Token:
    """One token of a source file with its contract clauses and comments erased.

    Attributes:
        text (str): The token. A preprocessor directive is a single token holding its whole
            logical line, whitespace-normalized, starting with `#`. The token of an `#if 0` also
            holds the dead branch after it, up to the directive that ends that branch.
        offset (int): The byte offset the token starts at.
        end (int): The byte offset just past the token.
        depth (int): The brace depth before the token.
    """

    text: str
    offset: int
    end: int
    depth: int

    @property
    def is_directive(self) -> bool:
        """True iff the token is a preprocessor directive."""
        return self.text.startswith("#")


@dataclass(frozen=True)
class _Item:
    """One top-level construct of a file: a directive, a function definition or a declaration.

    Attributes:
        tokens (tuple[str, ...]): The texts of the item's tokens; two items are the same code iff
            these are equal.
        kind (str): `directive`, `function`, `declaration`, `linkage` for the opening or closing
            of an `extern "C" { ... }` block, or `incomplete` for trailing tokens that end the
            file without a terminator.
        name (str | None): The name the item introduces, when it can be told: the function a
            definition defines, the name a declaration declares, or the macro that a `#define`
            or `#undef` names. None for every other directive.
        start (int): The byte offset the item starts at in its source.
        end (int): The byte offset just past the item, contract clauses included.
        body_offsets (tuple[int, ...]): For a function, the offset of each `{` that opens its
            body: one, or one per preprocessor branch when the header differs between branches.
            Empty for every other kind.
        body_names (tuple[tuple[str, ...], ...]): For a function, the names that the header
            before each of `body_offsets` may define, in the same order: usually one, more when
            the header is ambiguous (see `_names_before_last_group`), none when no name can be
            told. Branches of a conditional can give one body different names. Empty for every
            other kind.
    """

    tokens: tuple[str, ...]
    kind: str
    name: str | None
    start: int
    end: int
    body_offsets: tuple[int, ...] = ()
    body_names: tuple[tuple[str, ...], ...] = ()


def function_body_offsets(source: bytes, function: str) -> list[int]:
    """Return the offset of the opening brace of the body of each definition of the given function.

    Args:
        source (bytes): A C source.
        function (str): A function's name, such as `main`.

    Returns:
        list[int]: The offsets in source order; empty when the function is not defined. There is
            one offset per definition (a function can be defined once per preprocessor branch)
            and, within a definition whose header differs between branches, one per header that
            names the function.
    """
    return [
        offset
        for item in _split_items(_tokenize(source))
        if item.kind == "function"
        for offset, names in zip(item.body_offsets, item.body_names, strict=True)
        if function in names
    ]


def _tokenize(source: bytes) -> list[_Token]:
    """Return the tokens of the given source file, with contract clauses and comments erased.

    Each branch of a preprocessor conditional starts at the brace depth that the conditional
    starts at, so a brace that every branch opens (as in a function header that differs between
    branches) is counted once. After `#endif`, the depth is the one that the first branch ends
    at, so a brace opened in one conditional and closed in a later one with the same condition
    is counted even when the first conditional has an empty `#else`. The dead branch of an
    `#if 0` is part of the token of the `#if 0`, so its braces are not counted.

    Args:
        source (bytes): The original C source.

    Returns:
        list[_Token]: The tokens in source order.
    """
    stripped, _ = strip_cbmc_clauses(source)
    text = stripped.decode("latin-1")  # one character per byte, so offsets are byte offsets
    # A directive is compared in full: a contract clause inside a macro body is part of the macro.
    original = source.decode("latin-1")
    tokens: list[_Token] = []
    position = 0
    depth = 0
    # For each enclosing `#if`: the depth it starts at, and the depth its first branch ends at
    # (None until that branch ends).
    conditionals: list[tuple[int, int | None]] = []
    # Inside the dead branch of an `#if 0`, the number of conditionals opened in it; else None.
    dead_nesting: int | None = None
    at_line_start = True
    length = len(text)
    while position < length:
        if at_line_start and text[position] == "#":
            # The original keeps the backslash of a line continuation inside a contract clause.
            end = logical_line_end(source, position)
            directive = _normalize_directive(original[position:end])
            head = _DIRECTIVE_HEAD.match(directive)
            keyword = head.group(1) if head else ""
            if dead_nesting is not None:
                if keyword in _CONDITIONAL_OPENERS:
                    dead_nesting += 1
                elif keyword == "endif" and dead_nesting > 0:
                    dead_nesting -= 1
                elif dead_nesting == 0 and keyword in _CONDITIONAL_CONTINUATIONS:
                    _absorb_dead_branch(tokens, original, position)
                    dead_nesting = None
                if dead_nesting is not None:
                    position = end
                    continue
            tokens.append(_Token(directive, position, end, depth))
            if keyword in _CONDITIONAL_OPENERS:
                conditionals.append((depth, None))
                if _DEAD_CONDITIONAL.fullmatch(directive):
                    dead_nesting = 0
            elif keyword in _CONDITIONAL_CONTINUATIONS - {"endif"} and conditionals:
                start, first_end = conditionals[-1]
                conditionals[-1] = (start, depth if first_end is None else first_end)
                depth = start
            elif keyword == "endif" and conditionals:
                _, first_end = conditionals.pop()
                if first_end is not None:
                    depth = first_end
            position = end
            continue
        match = _TOKEN.match(text, position)
        assert match is not None, "the last alternative matches any character"
        kind = match.lastgroup
        value = match.group()
        if kind == "newline":
            at_line_start = True
        elif kind not in ("space", "comment"):
            at_line_start = False
            if dead_nesting is None:
                tokens.append(_Token(value, position, match.end(), depth))
                if value == "{":
                    # The brace of `extern "C" {` groups declarations without nesting them.
                    depth += 0 if _opens_linkage_block(tokens) else 1
                elif value == "}":
                    depth = max(depth - 1, 0)
        position = match.end()
    if dead_nesting is not None:
        _absorb_dead_branch(tokens, original, length)
    return tokens


def _absorb_dead_branch(tokens: list[_Token], original: str, end: int) -> None:
    """Extend the last token, an `#if 0`, over its dead branch, which ends at `end`.

    The dead branch stays part of a token, so a change to the dead code changes the code, but
    its braces and semicolons do not delimit items.

    Args:
        tokens (list[_Token]): Tokens ending with the token of an `#if 0`; modified in place.
        original (str): The text that the tokens' offsets index.
        end (int): The offset of the directive that ends the dead branch, or the end of the text
            when no directive does.
    """
    start = tokens[-1].offset
    tokens[-1] = _Token(_normalize_directive(original[start:end]), start, end, tokens[-1].depth)


def _normalize_directive(text: str) -> str:
    """Return a directive's text with each run of whitespace and comments replaced by one space.

    String and character literals are kept verbatim.

    Args:
        text (str): The directive's logical line.

    Returns:
        str: The directive, starting with `#` immediately followed by its keyword.
    """
    parts: list[str] = []
    separated = False
    for match in _TOKEN.finditer(text):
        if match.lastgroup in ("space", "newline", "comment"):
            separated = True
            continue
        if separated and parts:
            parts.append(" ")
        parts.append(match.group())
        separated = False
    return re.sub(r"^#\s+", "#", "".join(parts))


def _opens_linkage_block(tokens: list[_Token]) -> bool:
    """Return True iff `tokens` ends with the opening of an `extern "..." {` block.

    Args:
        tokens (list[_Token]): Tokens ending with a `{`.

    Returns:
        bool: True iff the brace follows `extern` and a string literal.
    """
    return (
        len(tokens) >= 3
        and tokens[-1].text == "{"
        and tokens[-2].text.startswith('"')
        and tokens[-3].text == "extern"
    )


def _split_items(tokens: list[_Token]) -> list[_Item]:
    """Return the top-level items of the given file, grouping its tokens by brace depth alone.

    A directive at file scope is an item by itself unless it interrupts one (a conditional around
    part of a declaration stays inside that declaration). A function definition ends at the brace
    that closes its body; every other item ends at a `;` at file scope. The opening and the
    closing brace of an `extern "C" { ... }` block are items of their own, so the declarations
    inside the block are split like any others.

    Args:
        tokens (list[_Token]): The file's tokens.

    Returns:
        list[_Item]: The items in source order.
    """
    items: list[_Item] = []
    pending: list[_Token] = []
    body_offsets: list[int] = []  # The `{` tokens in `pending` that open a function body.
    in_function_body = False
    for token in tokens:
        if token.is_directive and not pending and token.depth == 0:
            items.append(_make_item([token], "directive"))
            continue
        pending.append(token)
        if token.depth == 0 and token.text == "{":
            if _opens_linkage_block(pending):
                items.append(_make_item(pending, "linkage"))
                pending = []
            else:
                in_function_body = _opens_function_body(pending[:-1])
                if in_function_body:
                    body_offsets.append(token.offset)
        elif token.depth == 0 and token.text == "}":
            items.append(_make_item(pending, "linkage"))
            pending = []
        elif token.depth == 1 and token.text == "}" and in_function_body:
            items.append(_make_item(pending, "function", tuple(body_offsets)))
            pending = []
            body_offsets = []
            in_function_body = False
        elif token.depth == 0 and token.text == ";":
            items.append(_make_item(pending, "declaration"))
            pending = []
            body_offsets = []
    if pending:
        items.append(_make_item(pending, "incomplete"))
    return items


def _opens_function_body(prefix: list[_Token]) -> bool:
    """Return True iff a file-scope `{` after `prefix` opens a function body.

    It does not when the item has an initializer (`= {`), or names a `struct`, `union` or `enum`
    with no parameter list after the keyword (a type definition). Only keywords outside
    parentheses count: the `struct` in `void f(struct S *s) {` belongs to a parameter, and the
    brace still opens the body of `f`. Attributes are ignored, so the parentheses of
    `struct __attribute__((packed)) S {` are not a parameter list, and neither are those of an
    attribute macro, as in `struct ALIGNED(8) S {`.

    Args:
        prefix (list[_Token]): The item's tokens before the `{`.

    Returns:
        bool: True iff the brace opens a function body.
    """
    texts = _without_attributes([token.text for token in prefix if not token.is_directive])
    if "=" in texts:
        return False
    last_aggregate = -1
    parentheses = 0
    for index, text in enumerate(texts):
        if text == "(":
            parentheses += 1
        elif text == ")":
            parentheses = max(parentheses - 1, 0)
        elif parentheses == 0 and text in _AGGREGATE_KEYWORDS:
            last_aggregate = index
    after = texts[last_aggregate + 1 :]
    return "(" in after and not (last_aggregate >= 0 and _is_aggregate_head(after))


def _is_aggregate_head(texts: list[str]) -> bool:
    """Return True iff `texts` can be what follows `struct`, `union` or `enum` before its body.

    That is zero or more macro calls, such as `ALIGNED(8)`, then an optional tag. In
    `struct S f(void)`, by contrast, an identifier is followed by neither `(` nor the end.

    Args:
        texts (list[str]): Tokens that follow an aggregate keyword.

    Returns:
        bool: True iff `texts` is a sequence of macro calls followed by at most one identifier.
    """
    index = 0
    while index < len(texts):
        if not _IDENTIFIER.match(texts[index]):
            return False
        if index + 1 == len(texts):
            return True
        if texts[index + 1] != "(":
            return False
        closing = _group_end(texts, index + 1)
        if closing is None:
            return False
        index = closing + 1
    return True


def _group_end(texts: Sequence[str], opening: int) -> int | None:
    """Return the index of the `)` that closes the `(` at index `opening` of `texts`.

    Args:
        texts (Sequence[str]): Tokens.
        opening (int): The index of a `(` in `texts`.

    Returns:
        int | None: The index of the matching `)`, or None when the group is not closed.
    """
    depth = 0
    for index in range(opening, len(texts)):
        if texts[index] == "(":
            depth += 1
        elif texts[index] == ")":
            depth -= 1
            if depth == 0:
                return index
    return None


def _group_start(texts: Sequence[str], closing: int) -> int | None:
    """Return the index of the `(` that the `)` at index `closing` of `texts` closes.

    Args:
        texts (Sequence[str]): Tokens.
        closing (int): The index of a `)` in `texts`.

    Returns:
        int | None: The index of the matching `(`, or None when the group is not opened.
    """
    depth = 0
    for index in range(closing, -1, -1):
        if texts[index] == ")":
            depth += 1
        elif texts[index] == "(":
            depth -= 1
            if depth == 0:
                return index
    return None


def _make_item(tokens: list[_Token], kind: str, body_offsets: tuple[int, ...] = ()) -> _Item:
    """Return the item that consists of the given tokens.

    Args:
        tokens (list[_Token]): An item's tokens.
        kind (str): The item's kind.
        body_offsets (tuple[int, ...]): For a function, the offsets of the braces that open its
            body; at least one.

    Returns:
        _Item: The item, with the name it introduces when that can be told.
    """
    texts = tuple(token.text for token in tokens)
    body_names: tuple[tuple[str, ...], ...] = ()
    if kind == "directive":
        match = _DIRECTIVE_HEAD.match(texts[0])
        # `#ifdef X` tests X and `#if defined(X)` would give `defined`; neither introduces a name.
        name = match.group(2) or None if match and match.group(1) in _NAMING_DIRECTIVES else None
    elif kind == "function":
        # The parameter list is the last parenthesized group before the body, once attributes
        # are removed. A `{` before the body, as in a `struct` defined in the return type, does
        # not end the search. An unknown macro after the parameter list, as in
        # `int f(int x) REQUIRES(x > 0) {`, cannot be told from a macro return type, so both
        # names are kept as candidates; the item's name is the first. Each body brace gets the
        # names from its own header, which ends just before it: with `#ifdef X` / `int f(...) {`
        # / `#else` / `int g(...) {` / `#endif`, the first brace opens `f` and the second `g`.
        body_indexes = [
            index for index, token in enumerate(tokens) if token.offset in body_offsets
        ]
        body_names = tuple(
            _names_before_last_group(_without_attributes(list(texts[:index])))
            for index in body_indexes
        )
        name = body_names[0][0] if body_names[0] else None
    elif kind == "declaration":
        name = _declared_name(texts)
    else:
        name = None
    return _Item(
        tokens=texts,
        kind=kind,
        name=name,
        start=tokens[0].offset,
        end=tokens[-1].end,
        body_offsets=body_offsets,
        body_names=body_names,
    )


def _names_before_last_group(texts: Sequence[str]) -> tuple[str, ...]:
    """Return the names that a function header ending with a parenthesized group may define.

    The first is `_name_before_last_group`. When that name directly follows a `)`, as in
    `int f(int x) REQUIRES(x > 0)`, it may be a macro after the real parameter list or the
    function's name after a macro return type, as in `LIST(int) f(void)`; without preprocessing
    the two cannot be told apart, so the name before the earlier group is a candidate as well,
    and so on for each further macro.

    Args:
        texts (Sequence[str]): Tokens ending, possibly after directives, with a `)`.

    Returns:
        tuple[str, ...]: The candidate names, the likeliest first; empty when there is none.
    """
    names: list[str] = []
    while (name := _name_before_last_group(texts)) is not None:
        names.append(name)
        closing = max(index for index, text in enumerate(texts) if text == ")")
        opening = _group_start(texts, closing)
        assert opening is not None  # _name_before_last_group found the group.
        if opening < 2 or texts[opening - 1] != name or texts[opening - 2] != ")":
            break
        texts = texts[: opening - 1]
    return tuple(names)


def _name_before_last_group(texts: Sequence[str]) -> str | None:
    """Return the name of the function whose parameter list is the last parenthesized group.

    The name is usually the identifier before that group. When a parenthesized declarator
    precedes the group instead, the name is inside that declarator: in `int (*get(void))(int)`, a
    function that returns a function pointer, it is `get`, and in `int (f)(int x)` it is `f`.

    Args:
        texts (Sequence[str]): Tokens ending, possibly after directives, with a `)`.

    Returns:
        str | None: The name, or None when there is no such group or name.
    """
    closing = len(texts) - 1
    while closing >= 0 and texts[closing] != ")":
        closing -= 1
    if closing < 0:
        return None
    opening = _group_start(texts, closing)
    if opening is None or opening == 0:
        return None
    candidate = texts[opening - 1]
    if candidate == ")":
        declarator_start = _group_start(texts, opening - 1)
        if declarator_start is None:
            return None
        declarator = texts[declarator_start + 1 : opening - 1]
        if len(declarator) == 1:
            return declarator[0] if _IDENTIFIER.match(declarator[0]) else None
        return _name_before_last_group(declarator)
    return candidate if _IDENTIFIER.match(candidate) else None


def _declared_name(texts: tuple[str, ...]) -> str | None:
    """Return the name a file-scope declaration introduces, when it can be told.

    Handles the shapes agents write for helpers: a tagged `struct`/`union`/`enum` definition, a
    `typedef`, a function prototype, and a variable with or without an initializer.

    Args:
        texts (tuple[str, ...]): The declaration's tokens, ending with `;`.

    Returns:
        str | None: The name, or None when the shape is not recognised.
    """
    body = [text for text in texts[:-1] if not text.startswith("#")]
    if not body:
        return None
    top_level = _without_attributes(_top_level(body))
    # A tag, possibly after attribute macros, as in `struct ALIGNED(8) S { int a; };`.
    if top_level and top_level[0] in _AGGREGATE_KEYWORDS and _is_aggregate_head(top_level[1:]):
        tag = top_level[-1]
        return tag if len(top_level) > 1 and _IDENTIFIER.match(tag) else None
    if "=" in top_level:
        top_level = top_level[: top_level.index("=")]
    # A pointer declarator `( * name )` outside every parameter list, as in `int (*fp)(int);`.
    parentheses = 0
    for index, text in enumerate(top_level):
        if (
            parentheses == 0
            and top_level[index : index + 2] == ["(", "*"]
            and top_level[index + 3 : index + 4] == [")"]
        ):
            return top_level[index + 2] if _IDENTIFIER.match(top_level[index + 2]) else None
        if text == "(":
            parentheses += 1
        elif text == ")":
            parentheses = max(parentheses - 1, 0)
    if body[0] != "typedef" and "(" in top_level:
        candidate = top_level[top_level.index("(") - 1] if top_level.index("(") > 0 else ""
        return candidate if _IDENTIFIER.match(candidate) else None
    if "[" in top_level:
        top_level = top_level[: top_level.index("[")]
    candidate = top_level[-1] if top_level else ""
    # An anonymous `enum { ... };` has no name.
    if candidate in _AGGREGATE_KEYWORDS:
        return None
    return candidate if _IDENTIFIER.match(candidate) else None


def _top_level(texts: list[str]) -> list[str]:
    """Return the tokens of a declaration that are outside every brace group.

    Parentheses and brackets are kept, so a declarator can still be read; only `{ ... }` groups
    (a struct body, an initializer list) are dropped.

    Args:
        texts (list[str]): The declaration's tokens.

    Returns:
        list[str]: The tokens at brace depth zero.
    """
    kept: list[str] = []
    depth = 0
    for text in texts:
        if text == "{":
            depth += 1
        elif text == "}":
            depth -= 1
        elif depth == 0:
            kept.append(text)
    return kept


def _without_attributes(texts: list[str]) -> list[str]:
    """Return the tokens of a declaration without its attributes and asm labels.

    Args:
        texts (list[str]): The declaration's tokens.

    Returns:
        list[str]: The tokens, without each keyword in `_ATTRIBUTE_KEYWORDS` that is followed by a
            parenthesized group, and without that group.
    """
    kept: list[str] = []
    index = 0
    while index < len(texts):
        if texts[index] in _ATTRIBUTE_KEYWORDS and texts[index + 1 : index + 2] == ["("]:
            parentheses = 0
            index += 1
            while index < len(texts):
                if texts[index] == "(":
                    parentheses += 1
                elif texts[index] == ")":
                    parentheses -= 1
                index += 1
                if parentheses == 0:
                    break
            continue
        kept.append(texts[index])
        index += 1
    return kept
