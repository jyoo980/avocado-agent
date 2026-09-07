"""Splice one function's finished contract from a session's private copy into the canonical file.

`avocado_verify` can run several `claude -p` sessions at once, each on a private copy of the source
directory. When a session for function F ends, only F's definition (its contract clauses and,
exactly as today, anything the agent did inside F's body) and the *new* top-level items the agent
added for that contract -- helper functions, `#define`s, includes, declarations -- are carried into
the canonical file. Edits to any other existing definition are dropped: that is the isolation rule
that makes concurrent sessions safe, since every other definition in the canonical file may belong
to a session that is still running or has already finished.

Everything here works on bytes and byte offsets of the *original* source. Parsing goes through
`tools.util.tree_sitter_utils._parse_to_ast`, which blanks CBMC clauses to whitespace of identical
length before parsing, so every node offset indexes the original bytes; but for the same reason a
node's own `text` attribute is the blanked buffer and must never be used -- all text is sliced from
the original with `source[start_byte:end_byte]`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from tools.util.tree_sitter_utils import (
    _get_function_definition_name,
    _parse_to_ast,
    dfs_traversal,
)

if TYPE_CHECKING:
    from tree_sitter import Node

# Preprocessor nodes whose children are top-level items of the file; the enumerator descends into
# them so a function inside `#ifdef ... #endif` is found and its guard recorded.
_CONTAINER_KINDS = frozenset({"preproc_if", "preproc_ifdef", "preproc_elif", "preproc_else"})

# Tokens that appear as children of the containers above and are not items.
_CONTAINER_TOKENS = frozenset({"#if", "#ifdef", "#ifndef", "#elif", "#else", "#endif", "\n"})

# Root children that are never diffed or transplanted.
_IGNORED_KINDS = frozenset({"comment"})

# Specifiers that tree-sitter separates from their trailing `;`, which is glued back on.
_SPECIFIER_KINDS = frozenset({"struct_specifier", "union_specifier", "enum_specifier"})

# Declarator wrappers descended to reach the identifier a declaration introduces.
_DECLARATOR_FIELD = "declarator"


@dataclass(frozen=True)
class TopLevelItem:
    """One top-level construct of a C file, with its span in the original bytes.

    Attributes:
        kind (str): The tree-sitter node type (`function_definition`, `preproc_def`, ...).
        key (tuple[str, str]): The identity used to compare the same construct across versions of
            the file, e.g. `("function", "swap")` or `("macro", "CAP")`; see `_item_key`.
        start_byte (int): Inclusive start offset in the original source.
        end_byte (int): Exclusive end offset in the original source.
        text (bytes): `source[start_byte:end_byte]`, sliced from the original bytes.
        guard (tuple[str, ...]): Header lines of the enclosing preprocessor conditionals,
            outermost first; empty at file scope.
    """

    kind: str
    key: tuple[str, str]
    start_byte: int
    end_byte: int
    text: bytes
    guard: tuple[str, ...]


@dataclass(frozen=True)
class FunctionSpan:
    """The byte span of one function definition and of its body.

    Attributes:
        start_byte (int): Inclusive start of the definition (return type onwards).
        end_byte (int): Exclusive end of the definition (past the closing brace).
        body_start_byte (int): Offset of the body's opening brace.
        body_end_byte (int): Exclusive end of the body.
        guard (tuple[str, ...]): Enclosing preprocessor conditionals, as in `TopLevelItem`.
    """

    start_byte: int
    end_byte: int
    body_start_byte: int
    body_end_byte: int
    guard: tuple[str, ...]


@dataclass(frozen=True)
class MergeReport:
    """What a merge kept, what it dropped, and why it failed, for the run log.

    Attributes:
        merged (bool): True iff the canonical file was updated.
        reason (str): Why the merge failed; empty on success.
        transplanted (list[str]): New top-level items carried into the canonical file, rendered
            as `kind:name`.
        skipped_duplicates (list[str]): New items that another session had already added with
            identical text, so nothing needed doing.
        dropped (list[str]): Edits outside the function that the isolation rule discarded
            (modified or removed existing items), rendered as `kind:name`.
        body_changed (bool): True iff the function's body differs from the fork base. Body
            edits are carried, as they are today; this is a diagnostic.
    """

    merged: bool
    reason: str = ""
    transplanted: list[str] = field(default_factory=list)
    skipped_duplicates: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    body_changed: bool = False

    def to_record(self) -> dict:
        """Return a JSON-serializable form of this report for the run log.

        Returns:
            dict: The report's fields.
        """
        return {
            "merged": self.merged,
            "reason": self.reason,
            "transplanted": list(self.transplanted),
            "skipped_duplicates": list(self.skipped_duplicates),
            "dropped": list(self.dropped),
            "body_changed": self.body_changed,
        }


def iter_top_level_items(source: bytes) -> list[TopLevelItem]:
    """Return the file's top-level constructs in source order.

    Preprocessor conditionals are flattened (their members are items, tagged with the guard),
    a `;` that tree-sitter separates from a `struct`/`union`/`enum` specifier is glued back onto
    it, and comments are skipped.

    Args:
        source (bytes): The original C source.

    Returns:
        list[TopLevelItem]: The items, in source order.
    """
    tree = _parse_to_ast(source)
    items: list[TopLevelItem] = []
    _collect_items(tree.root_node, source, (), items)
    return items


def _collect_items(
    node: Node, source: bytes, guard: tuple[str, ...], items: list[TopLevelItem]
) -> None:
    """Append the items directly under `node` to `items`, descending into conditionals.

    Args:
        node (Node): The root node or a preprocessor container.
        source (bytes): The original C source.
        guard (tuple[str, ...]): The conditionals enclosing `node`.
        items (list[TopLevelItem]): Accumulator, extended in place.
    """
    # tree-sitter hands out a fresh wrapper object per access, so compare by span, not identity.
    skipped_spans = {
        (named.start_byte, named.end_byte)
        for named in (node.child_by_field_name("condition"), node.child_by_field_name("name"))
        if named is not None
    }
    for child in node.children:
        kind = child.type
        if (child.start_byte, child.end_byte) in skipped_spans or kind in _CONTAINER_TOKENS:
            continue
        if kind in _CONTAINER_KINDS:
            header = _first_line(source, child.start_byte, child.end_byte)
            _collect_items(child, source, (*guard, header), items)
            continue
        if kind in _IGNORED_KINDS:
            continue
        if kind == ";" and items and _only_whitespace_between(source, items[-1], child):
            previous = items[-1]
            if previous.kind in _SPECIFIER_KINDS or previous.kind == "declaration":
                items[-1] = TopLevelItem(
                    kind=previous.kind,
                    key=previous.key,
                    start_byte=previous.start_byte,
                    end_byte=child.end_byte,
                    text=source[previous.start_byte : child.end_byte],
                    guard=previous.guard,
                )
                continue
        items.append(
            TopLevelItem(
                kind=kind,
                key=_item_key(child, source),
                start_byte=child.start_byte,
                end_byte=child.end_byte,
                text=source[child.start_byte : child.end_byte],
                guard=guard,
            )
        )


def _only_whitespace_between(source: bytes, item: TopLevelItem, node: Node) -> bool:
    """Return True iff nothing but whitespace separates `item` from `node`.

    Args:
        source (bytes): The original C source.
        item (TopLevelItem): The preceding item.
        node (Node): The following node.

    Returns:
        bool: True iff only whitespace lies between them.
    """
    return item.end_byte <= node.start_byte and not source[item.end_byte : node.start_byte].strip()


def _first_line(source: bytes, start_byte: int, end_byte: int) -> str:
    """Return the first line of `source[start_byte:end_byte]`, stripped.

    Args:
        source (bytes): The original C source.
        start_byte (int): Inclusive start offset.
        end_byte (int): Exclusive end offset.

    Returns:
        str: The first line, decoded leniently.
    """
    text = source[start_byte:end_byte]
    newline = text.find(b"\n")
    line = text if newline < 0 else text[:newline]
    return line.decode("utf-8", errors="replace").strip()


def _item_key(node: Node, source: bytes) -> tuple[str, str]:
    """Return the identity key of a top-level node.

    Functions and macros are keyed by name, declarations and typedefs by the identifier they
    introduce, struct/union/enum specifiers by their tag, and everything else (includes, pragmas,
    parse errors) by their whitespace-normalized text.

    Args:
        node (Node): The top-level node.
        source (bytes): The original C source, for slicing names and text.

    Returns:
        tuple[str, str]: `(category, name-or-text)`.
    """
    kind = node.type
    if kind == "function_definition":
        try:
            return ("function", _get_function_definition_name(node))
        except AssertionError:
            return ("function", _normalize(_slice(source, node)).decode("utf-8", "replace"))
    if kind in ("preproc_def", "preproc_function_def"):
        name = node.child_by_field_name("name")
        if name is not None:
            return ("macro", _slice(source, name).decode("utf-8", "replace"))
    if kind in ("declaration", "type_definition"):
        category = "typedef" if kind == "type_definition" else "declaration"
        declared = _declared_name(node, source)
        if declared:
            return (category, declared)
        return (category, _normalize(_slice(source, node)).decode("utf-8", "replace"))
    if kind in _SPECIFIER_KINDS:
        name = node.child_by_field_name("name")
        if name is not None:
            return (kind, _slice(source, name).decode("utf-8", "replace"))
    if kind == "preproc_include":
        return ("include", _normalize(_slice(source, node)).decode("utf-8", "replace"))
    return (kind, _normalize(_slice(source, node)).decode("utf-8", "replace"))


def _declared_name(node: Node, source: bytes) -> str | None:
    """Return the identifier a declaration or typedef introduces, if it can be found.

    Descends through `declarator` fields (`init_declarator`, `pointer_declarator`,
    `function_declarator`, `array_declarator`, ...) to the identifier. For a declaration with
    several declarators the first one names the item.

    Args:
        node (Node): A `declaration` or `type_definition` node.
        source (bytes): The original C source.

    Returns:
        str | None: The declared identifier, or None if the shape is not recognised.
    """
    declarator = node.child_by_field_name(_DECLARATOR_FIELD)
    while declarator is not None and declarator.type not in ("identifier", "type_identifier"):
        declarator = declarator.child_by_field_name(_DECLARATOR_FIELD)
    if declarator is None:
        return None
    return _slice(source, declarator).decode("utf-8", "replace")


def _slice(source: bytes, node: Node) -> bytes:
    """Return the original bytes covered by `node`.

    Args:
        source (bytes): The original C source.
        node (Node): Any node of a tree parsed from `source`.

    Returns:
        bytes: `source[node.start_byte:node.end_byte]`.
    """
    return source[node.start_byte : node.end_byte]


def _normalize(text: bytes) -> bytes:
    """Collapse runs of whitespace so that reformatting does not count as a modification.

    Args:
        text (bytes): Source text.

    Returns:
        bytes: The text with every whitespace run replaced by a single space, and trimmed.
    """
    return b" ".join(text.split())


def find_function_span(source: bytes, function: str) -> FunctionSpan | None:
    """Return the span of `function`'s definition, or None when it cannot be trusted.

    None is returned when the function is absent or defined more than once, when its node sits
    under a parse error, or when the span does not re-parse on its own as exactly that one
    definition -- tree-sitter's error recovery can start a definition node early and swallow the
    item before it, and such a span must never be spliced into another file.

    Args:
        source (bytes): The original C source.
        function (str): The function name.

    Returns:
        FunctionSpan | None: The span, or None.
    """
    tree = _parse_to_ast(source)
    matches = []
    for node in dfs_traversal(tree.root_node):
        if node.type != "function_definition":
            continue
        try:
            name = _get_function_definition_name(node)
        except AssertionError:
            continue
        if name == function:
            matches.append(node)
    if len(matches) != 1:
        return None
    node = matches[0]
    guard: list[str] = []
    parent = node.parent
    while parent is not None:
        if parent.type == "ERROR":
            return None
        if parent.type in _CONTAINER_KINDS:
            guard.append(_first_line(source, parent.start_byte, parent.end_byte))
        parent = parent.parent
    body = node.child_by_field_name("body")
    if body is None:
        return None
    if not _span_is_self_contained(_slice(source, node), function):
        return None
    return FunctionSpan(
        start_byte=node.start_byte,
        end_byte=node.end_byte,
        body_start_byte=body.start_byte,
        body_end_byte=body.end_byte,
        guard=tuple(reversed(guard)),
    )


def _span_is_self_contained(text: bytes, function: str) -> bool:
    """Return True iff `text`, parsed alone, is exactly one clean definition of `function`.

    Args:
        text (bytes): A candidate definition span.
        function (str): The expected function name.

    Returns:
        bool: True iff the span is a single, error-free definition of `function`.
    """
    tree = _parse_to_ast(text)
    root = tree.root_node
    if root.has_error:
        return False
    definitions = [child for child in root.children if child.type not in _IGNORED_KINDS]
    if len(definitions) != 1 or definitions[0].type != "function_definition":
        return False
    try:
        return _get_function_definition_name(definitions[0]) == function
    except AssertionError:
        return False


def merge_function(
    *, canonical: bytes, snapshot: bytes, fork_base: bytes, function: str
) -> tuple[bytes, MergeReport]:
    """Splice `function`'s definition and its new helpers from `snapshot` into `canonical`.

    `snapshot` is the session's copy of the file after the agent finished; `fork_base` is the
    canonical file as it was when that copy was taken, which tells the merge what the agent
    changed; `canonical` is the file as it is now, possibly already carrying other sessions'
    merges. Offsets in `canonical` are taken from a fresh parse, so earlier merges are accounted
    for; offsets in `snapshot` are independent of it.

    Args:
        canonical (bytes): The current canonical file.
        snapshot (bytes): The session's finished copy.
        fork_base (bytes): The canonical file at fork time.
        function (str): The function the session worked on.

    Returns:
        tuple[bytes, MergeReport]: The merged file (or `canonical` unchanged on failure) and the
            report of what was kept and dropped.
    """
    snapshot_span = find_function_span(snapshot, function)
    if snapshot_span is None:
        return canonical, MergeReport(
            False, f"{function} is missing, duplicated or ill-formed in the session copy"
        )
    canonical_span = find_function_span(canonical, function)
    if canonical_span is None:
        return canonical, MergeReport(
            False, f"{function} is missing, duplicated or ill-formed in the canonical file"
        )

    own_key = ("function", function)
    base_by_key = _index(iter_top_level_items(fork_base))
    canonical_by_key = _index(iter_top_level_items(canonical))
    snapshot_items = iter_top_level_items(snapshot)

    dropped: list[str] = []
    transplant: list[TopLevelItem] = []
    skipped: list[str] = []
    seen: set[tuple[str, str]] = set()
    for item in snapshot_items:
        if item.key == own_key:
            continue
        seen.add(item.key)
        base_text = base_by_key.get(item.key)
        if base_text is not None:
            if _normalize(item.text) != base_text:
                dropped.append(_render(item.key))
            continue
        canonical_text = canonical_by_key.get(item.key)
        if canonical_text is not None:
            if _normalize(item.text) == canonical_text:
                skipped.append(_render(item.key))
                continue
            return canonical, MergeReport(
                False,
                f"{_render(item.key)} is already defined differently in the canonical file",
                dropped=dropped,
            )
        if item.guard != snapshot_span.guard:
            dropped.append(f"{_render(item.key)} (guard {item.guard!r} not carried)")
        transplant.append(item)
    dropped.extend(_render(key) for key in base_by_key if key != own_key and key not in seen)

    base_span = find_function_span(fork_base, function)
    body_changed = base_span is not None and _normalize(
        snapshot[snapshot_span.body_start_byte : snapshot_span.body_end_byte]
    ) != _normalize(fork_base[base_span.body_start_byte : base_span.body_end_byte])

    prefix = b"".join(item.text.rstrip(b"\n") + b"\n\n" for item in transplant)
    merged = (
        canonical[: canonical_span.start_byte]
        + prefix
        + snapshot[snapshot_span.start_byte : snapshot_span.end_byte]
        + canonical[canonical_span.end_byte :]
    )
    return merged, MergeReport(
        True,
        transplanted=[_render(item.key) for item in transplant],
        skipped_duplicates=skipped,
        dropped=dropped,
        body_changed=body_changed,
    )


def _index(items: list[TopLevelItem]) -> dict[tuple[str, str], bytes]:
    """Return a map from item key to normalized text; on duplicate keys the first item wins.

    Args:
        items (list[TopLevelItem]): Items in source order.

    Returns:
        dict[tuple[str, str], bytes]: Key to normalized text.
    """
    indexed: dict[tuple[str, str], bytes] = {}
    for item in items:
        indexed.setdefault(item.key, _normalize(item.text))
    return indexed


def _render(key: tuple[str, str]) -> str:
    """Return a short human-readable form of an item key for logs and reports.

    Args:
        key (tuple[str, str]): The item key.

    Returns:
        str: `category:name`, with long text-based names truncated.
    """
    category, name = key
    if len(name) > 40:
        name = name[:37] + "..."
    return f"{category}:{name}"
