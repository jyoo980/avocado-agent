"""Split a C file into top-level items without parsing it, and locate function bodies.

Contract clauses and comments are erased, the file is split into tokens and then into top-level
items by brace depth alone. That is robust to constructs the C grammar cannot parse
(`__CPROVER_forall { ... }` in a helper, attribute macros, operator macros).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from tools.util.cbmc_clause_stripper import strip_cbmc_clauses
from tools.util.preproc_blanker import _logical_line_end

_TOKEN = re.compile(
    r"""
      (?P<space>[ \t\r\f\v]+|\\\n)
    | (?P<newline>\n)
    | (?P<comment>/\*.*?\*/|//[^\n]*)
    | (?P<literal>"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*')
    | (?P<word>[A-Za-z_0-9]+)
    | (?P<operator>>>=|<<=|\.\.\.|->|\+\+|--|<<|>>|<=|>=|==|!=|&&|\|\||[-+*/%&^|]=|\#\#|.)
    """,
    re.VERBOSE | re.DOTALL,
)
_DIRECTIVE_HEAD = re.compile(r"#\s*(\w*)\s*(\w*)")
_IDENTIFIER = re.compile(r"[A-Za-z_]\w*\Z")
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
            logical line, whitespace-normalized, starting with `#`.
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
        name (str | None): The name the item introduces, when it can be told.
        start (int): The byte offset the item starts at in its source.
        end (int): The byte offset just past the item, contract clauses included.
        body_offsets (tuple[int, ...]): For a function, the offset of each `{` that opens its
            body: one, or one per preprocessor branch when the header differs between branches.
            Empty for every other kind.
    """

    tokens: tuple[str, ...]
    kind: str
    name: str | None
    start: int
    end: int
    body_offsets: tuple[int, ...] = ()


def function_body_offsets(source: bytes, function: str) -> list[int]:
    """Return the offset of the opening brace of the body of each definition of a function.

    Args:
        source (bytes): A C source.
        function (str): The function's name.

    Returns:
        list[int]: The offsets in source order; empty when the function is not defined. There is
            one offset per definition (a function can be defined once per preprocessor branch)
            and, within a definition whose header differs between branches, one per header.
    """
    return [
        offset
        for item in _split_items(_tokenize(source))
        if item.kind == "function" and item.name == function
        for offset in item.body_offsets
    ]


def _tokenize(source: bytes) -> list[_Token]:
    """Split a source file into tokens, with contract clauses and comments erased.

    Each branch of a preprocessor conditional starts at the brace depth that the conditional
    starts at, so a brace that every branch opens (as in a function header that differs between
    branches) is counted once. After `#endif`, the depth is the one that the first branch ends
    at, so a brace opened in one conditional and closed in a later one with the same condition
    is counted even when the first conditional has an empty `#else`.

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
    at_line_start = True
    length = len(text)
    while position < length:
        if at_line_start and text[position] == "#":
            # The original keeps the backslash of a line continuation inside a contract clause.
            end = _logical_line_end(source, position)
            directive = _normalize_directive(original[position:end])
            tokens.append(_Token(directive, position, end, depth))
            head = _DIRECTIVE_HEAD.match(directive)
            keyword = head.group(1) if head else ""
            if keyword in ("if", "ifdef", "ifndef"):
                conditionals.append((depth, None))
            elif keyword in ("elif", "elifdef", "elifndef", "else") and conditionals:
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
            tokens.append(_Token(value, position, match.end(), depth))
            if value == "{":
                # The brace of `extern "C" {` groups declarations without nesting them.
                depth += 0 if _opens_linkage_block(tokens) else 1
            elif value == "}":
                depth = max(depth - 1, 0)
        position = match.end()
    return tokens


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
    """Group a file's tokens into its top-level items, by brace depth alone.

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
    `struct __attribute__((packed)) S {` are not a parameter list.

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
    return "(" in texts[last_aggregate + 1 :]


def _make_item(tokens: list[_Token], kind: str, body_offsets: tuple[int, ...] = ()) -> _Item:
    """Build an item from its tokens.

    Args:
        tokens (list[_Token]): The item's tokens.
        kind (str): The item's kind.
        body_offsets (tuple[int, ...]): For a function, the offsets of the braces that open its
            body; at least one.

    Returns:
        _Item: The item, with the name it introduces when that can be told.
    """
    texts = tuple(token.text for token in tokens)
    if kind == "directive":
        match = _DIRECTIVE_HEAD.match(texts[0])
        name = match.group(2) or None if match else None
    elif kind == "function":
        # The parameter list is the last parenthesized group before the body. A `{` before the
        # body, as in a `struct` defined in the return type, does not end the search.
        body = next(index for index, token in enumerate(tokens) if token.offset == body_offsets[0])
        name = _name_before_last_group(texts[:body])
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
    )


def _name_before_last_group(texts: tuple[str, ...]) -> str | None:
    """Return the identifier before the last parenthesized group in `texts`.

    Args:
        texts (tuple[str, ...]): Tokens ending, possibly after directives, with a `)`.

    Returns:
        str | None: The identifier, or None when there is no such group or name.
    """
    closing = len(texts) - 1
    while closing >= 0 and texts[closing] != ")":
        closing -= 1
    depth = 0
    for index in range(closing, -1, -1):
        if texts[index] == ")":
            depth += 1
        elif texts[index] == "(":
            depth -= 1
            if depth == 0:
                candidate = texts[index - 1] if index > 0 else ""
                return candidate if _IDENTIFIER.match(candidate) else None
    return None


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
