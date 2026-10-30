"""Split a C file into top-level items without parsing it, and locate function bodies.

Contract clauses and comments are erased, the file is split into tokens and then into top-level
items by brace depth alone. That is robust to constructs the C grammar cannot parse
(`__CPROVER_forall { ... }` in a helper, attribute macros, operator macros).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from tools.util.cbmc_clause_stripper import strip_cbmc_clauses

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
_COMMENT = re.compile(r"/\*.*?\*/|//[^\n]*", re.DOTALL)
_DIRECTIVE_HEAD = re.compile(r"#\s*(\w*)\s*(\w*)")
_IDENTIFIER = re.compile(r"[A-Za-z_]\w*\Z")
_AGGREGATE_KEYWORDS = frozenset({"struct", "union", "enum"})
# How many tokens of context a message quotes.
_QUOTED_TOKENS = 6


@dataclass(frozen=True)
class _Token:
    """One token of a source file with its contract clauses and comments erased.

    Attributes:
        text (str): The token. A preprocessor directive is a single token holding its whole
            logical line, whitespace-normalized, starting with `#`.
        line (int): The 1-based line the token starts on.
        offset (int): The byte offset the token starts at.
        end (int): The byte offset just past the token.
        depth (int): The brace depth before the token.
    """

    text: str
    line: int
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
        lines (tuple[int, ...]): The line of each token.
        kind (str): `directive`, `function`, `declaration`, `linkage` for the opening or closing
            of an `extern "C" { ... }` block, or `incomplete` for trailing tokens that end the
            file without a terminator.
        name (str | None): The name the item introduces, when it can be told.
        start (int): The byte offset the item starts at in its source.
        end (int): The byte offset just past the item, contract clauses included.
    """

    tokens: tuple[str, ...]
    lines: tuple[int, ...]
    kind: str
    name: str | None
    start: int
    end: int

    @property
    def line(self) -> int:
        """The line the item starts on."""
        return self.lines[0]

    @property
    def directive(self) -> str:
        """The directive's keyword (`define`, `if`, ...), or "" for other items."""
        if self.kind != "directive":
            return ""
        match = _DIRECTIVE_HEAD.match(self.tokens[0])
        return match.group(1) if match else ""

    def describe(self) -> str:
        """Return a short description of the item for a message.

        Returns:
            str: For example `function swap`, `macro CAP` or `the declaration of table`.
        """
        if self.kind == "function" and self.name:
            return f"function {self.name}"
        if self.kind == "directive":
            if self.directive == "define" and self.name:
                return f"macro {self.name}"
            return f"`{_quote(self.tokens)}`"
        if self.name:
            return f"the declaration of {self.name}"
        return f"`{_quote(self.tokens)}`"


def function_body_offsets(source: bytes, function: str) -> list[int]:
    """Return the offset of the opening brace of the body of each definition of a function.

    Args:
        source (bytes): A C source.
        function (str): The function's name.

    Returns:
        list[int]: One offset per definition (a function may be defined once per preprocessor
            branch), in source order; empty when the function is not defined.
    """
    tokens = _tokenize(source)
    offsets = []
    for item in _split_items(tokens):
        if item.kind != "function" or item.name != function:
            continue
        for token in tokens:
            if token.offset >= item.start and token.depth == 0 and token.text == "{":
                offsets.append(token.offset)
                break
    return offsets


def _tokenize(source: bytes) -> list[_Token]:
    """Split a source file into tokens, with contract clauses and comments erased.

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
    line = 1
    depth = 0
    at_line_start = True
    length = len(text)
    while position < length:
        if at_line_start and text[position] == "#":
            end = _logical_line_end(text, position)
            directive = _normalize_directive(original[position:end])
            tokens.append(_Token(directive, line, position, end, depth))
            line += text.count("\n", position, end)
            position = end
            continue
        match = _TOKEN.match(text, position)
        if match is None:  # cannot happen: the last alternative matches any character
            position += 1
            continue
        kind = match.lastgroup
        value = match.group()
        if kind == "newline":
            at_line_start = True
        elif kind not in ("space", "comment"):
            at_line_start = False
            tokens.append(_Token(value, line, position, match.end(), depth))
            if value == "{":
                # The brace of `extern "C" {` groups declarations without nesting them.
                depth += 0 if _opens_linkage_block(tokens) else 1
            elif value == "}":
                depth = max(depth - 1, 0)
        line += value.count("\n")
        position = match.end()
    return tokens


def _logical_line_end(text: str, start: int) -> int:
    """Return the offset just past the preprocessor directive that starts at `start`.

    A directive runs to the end of its line, continuing over backslash-newlines and over block
    comments that span lines.

    Args:
        text (str): The source text.
        start (int): The offset of the directive's `#`.

    Returns:
        int: The offset of the newline that ends the directive, or the end of the text.
    """
    position = start
    length = len(text)
    while position < length:
        character = text[position]
        if character == "\n":
            return position
        if character == "\\" and text.startswith("\\\n", position):
            position += 2
        elif text.startswith("/*", position):
            closing = text.find("*/", position + 2)
            position = length if closing < 0 else closing + 2
        elif text.startswith("//", position):
            newline = text.find("\n", position)
            return length if newline < 0 else newline
        else:
            position += 1
    return length


def _normalize_directive(text: str) -> str:
    """Return a directive's text with comments removed and whitespace collapsed.

    Args:
        text (str): The directive's logical line.

    Returns:
        str: The directive, starting with `#` immediately followed by its keyword.
    """
    words = _COMMENT.sub(" ", text).replace("\\\n", " ").split()
    normalized = " ".join(words)
    return re.sub(r"^#\s+", "#", normalized)


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
        elif token.depth == 0 and token.text == "}":
            items.append(_make_item(pending, "linkage"))
            pending = []
        elif token.depth == 1 and token.text == "}" and in_function_body:
            items.append(_make_item(pending, "function"))
            pending = []
            in_function_body = False
        elif token.depth == 0 and token.text == ";":
            items.append(_make_item(pending, "declaration"))
            pending = []
    if pending:
        items.append(_make_item(pending, "incomplete"))
    return items


def _opens_function_body(prefix: list[_Token]) -> bool:
    """Return True iff a file-scope `{` after `prefix` opens a function body.

    It does not when the item has an initializer (`= {`), or names a `struct`, `union` or `enum`
    with no parameter list after the keyword (a type definition). Only keywords outside
    parentheses count: the `struct` in `void f(struct S *s) {` belongs to a parameter, and the
    brace still opens the body of `f`.

    Args:
        prefix (list[_Token]): The item's tokens before the `{`.

    Returns:
        bool: True iff the brace opens a function body.
    """
    texts = [token.text for token in prefix if not token.is_directive]
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


def _make_item(tokens: list[_Token], kind: str) -> _Item:
    """Build an item from its tokens.

    Args:
        tokens (list[_Token]): The item's tokens.
        kind (str): The item's kind.

    Returns:
        _Item: The item, with the name it introduces when that can be told.
    """
    texts = tuple(token.text for token in tokens)
    if kind == "directive":
        match = _DIRECTIVE_HEAD.match(texts[0])
        name = match.group(2) or None if match else None
    elif kind == "function":
        name = _function_name(texts)
    elif kind == "declaration":
        name = _declared_name(texts)
    else:
        name = None
    return _Item(
        tokens=texts,
        lines=tuple(token.line for token in tokens),
        kind=kind,
        name=name,
        start=tokens[0].offset,
        end=tokens[-1].end,
    )


def _function_name(texts: tuple[str, ...]) -> str | None:
    """Return the name a function definition defines.

    The name is the identifier before the parameter list, and the parameter list is the last
    parenthesized group before the body.

    Args:
        texts (tuple[str, ...]): The definition's tokens.

    Returns:
        str | None: The name, or None when the shape is not recognised.
    """
    depth = 0
    body = None
    for index, text in enumerate(texts):
        if text == "{":
            if depth == 0:
                body = index
                break
            depth += 1
        elif text == "}":
            depth -= 1
    if body is None:
        return None
    return _name_before_last_group(texts[:body])


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
    top_level = _top_level(body)
    if (
        body[0] in _AGGREGATE_KEYWORDS
        and len(body) > 2
        and _IDENTIFIER.match(body[1])
        and body[2] == "{"
        and body[-1] == "}"
    ):
        return body[1]
    if "=" in top_level:
        top_level = top_level[: top_level.index("=")]
    for index in range(len(top_level) - 3):
        if top_level[index : index + 2] == ["(", "*"] and top_level[index + 3] == ")":
            return top_level[index + 2] if _IDENTIFIER.match(top_level[index + 2]) else None
    if body[0] != "typedef" and "(" in top_level:
        candidate = top_level[top_level.index("(") - 1] if top_level.index("(") > 0 else ""
        return candidate if _IDENTIFIER.match(candidate) else None
    if "[" in top_level:
        top_level = top_level[: top_level.index("[")]
    candidate = top_level[-1] if top_level else ""
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


def _quote(texts: tuple[str, ...]) -> str:
    """Return the first few tokens of `texts` as one string, for a message.

    Args:
        texts (tuple[str, ...]): Tokens.

    Returns:
        str: Up to `_QUOTED_TOKENS` tokens joined by spaces, with `...` when there are more.
    """
    if not texts:
        return "(end of the item)"
    quoted = " ".join(texts[:_QUOTED_TOKENS])
    if len(quoted) > 80:
        quoted = quoted[:77] + "..."
    return quoted + (" ..." if len(texts) > _QUOTED_TOKENS else "")
