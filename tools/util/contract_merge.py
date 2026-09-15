"""Splice one function's finished contract from a session's private copy into the canonical file.

`avocado_verify` can run several `claude -p` sessions at once, each on a private copy of the source
directory. When a session for function F ends, only F's definition (its contract clauses and,
exactly as today, anything the agent did inside F's body) and the *new* top-level items the agent
added for that contract -- helper functions, `#define`s, includes, declarations -- are carried into
the canonical file. Edits to other existing *functions* are carried too when the caller allows it
(`merge_function(frozen_functions=...)`): an agent proving a caller routinely fixes a callee's
contract in passing, and dropping that fix leaves a caller that verified in the copy but not in the
canonical file. A function that another session is running on is never carried, nor is one that
has changed in the canonical file since the fork; edits to other existing items (macros, typedefs,
declarations) are always dropped. Whether a carried edit is *kept* is the caller's decision: it
re-verifies what the edit can affect before committing (see `avocado_verify._merge_and_verify`).

Everything here works on bytes and byte offsets of the *original* source. Parsing goes through
`tools.util.tree_sitter_utils._parse_to_ast`, which blanks CBMC clauses to whitespace of identical
length before parsing, so every node offset indexes the original bytes; but for the same reason a
node's own `text` attribute is the blanked buffer and must never be used -- all text is sliced from
the original with `source[start_byte:end_byte]`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from itertools import pairwise
from typing import TYPE_CHECKING

from tools.util.tree_sitter_utils import (
    _get_function_definition_name,
    _parse_to_ast,
    dfs_traversal,
)

if TYPE_CHECKING:
    from collections.abc import Collection

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

# Comments and preprocessor directives (with backslash continuations), erased before a span's
# prefix is inspected; neither can hide a swallowed neighbour's terminator.
_COMMENT = re.compile(rb"/\*.*?\*/|//[^\n]*", re.DOTALL)
_DIRECTIVE_LINE = re.compile(rb"^[ \t]*#(?:[^\n\\]|\\\n)*$", re.MULTILINE)

# Tokens that never occur between the start of a function definition and its name -- a storage
# class, attribute macros and the return type contain none of them -- but that a top-level item
# swallowed by tree-sitter's error recovery nearly always leaves behind.
_PREFIX_BREAKERS = re.compile(rb"[;{}]")


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
            (modified or removed existing items), rendered as `kind:name`, with the reason in
            parentheses when there is a specific one.
        carried (list[str]): Other existing functions whose edited definitions were spliced in
            alongside the function's own (see `merge_function`).
        body_changed (bool): True iff the function's body differs from the fork base. Body
            edits are carried, as they are today; this is a diagnostic.
    """

    merged: bool
    reason: str = ""
    transplanted: list[str] = field(default_factory=list)
    skipped_duplicates: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    carried: list[str] = field(default_factory=list)
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
            "carried": list(self.carried),
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
        function_name = _get_function_definition_name(node)
        if function_name is not None:
            return ("function", function_name)
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
    under a parse error, or when the span does not re-parse on its own as that one definition
    and nothing else (see `_span_is_self_contained`) -- tree-sitter's error recovery can start a
    definition node early and swallow the item before it, and such a span must never be spliced
    into another file.

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
        if _get_function_definition_name(node) == function:
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
    """Return True iff `text`, parsed alone, is one definition of `function` and nothing else.

    tree-sitter parses unpreprocessed C, so a definition that uses a macro the grammar cannot
    read -- an attribute macro before the return type (`LZ4_FORCE_INLINE U32 f(...)`) or an
    operator macro in the body (`64 KB`) -- carries ERROR nodes even though its span is delimited
    correctly. Those errors are tolerated: the file is compiled with goto-cc and verified after
    the merge, which catches anything a lenient parse lets through.

    What is rejected is a span that is not exactly one `function_definition` of `function`
    covering the whole text apart from comments, one that does not end with the closing brace of
    the body, and one whose text before the function's name contains a `;`, `{` or `}` outside
    comments and directives: tree-sitter's error recovery can start a definition node inside the
    broken item before it, and a swallowed item nearly always leaves one of those tokens in the
    prefix, while a storage class, attribute macros and a return type never do.

    Args:
        text (bytes): A candidate definition span.
        function (str): The expected function name.

    Returns:
        bool: True iff the span is a single definition of `function`.
    """
    tree = _parse_to_ast(text)
    items = [child for child in tree.root_node.children if child.type not in _IGNORED_KINDS]
    if len(items) != 1 or items[0].type != "function_definition":
        return False
    node = items[0]
    if _get_function_definition_name(node) != function:
        return False
    if not text.rstrip().endswith(b"}"):
        return False
    outside = text[: node.start_byte] + text[node.end_byte :]
    if _COMMENT.sub(b"", outside).strip():
        return False
    prefix = _signature_prefix(text, function)
    return prefix is not None and _PREFIX_BREAKERS.search(prefix) is None


def _signature_prefix(text: bytes, function: str) -> bytes | None:
    """Return a definition span's text before `function`'s name, comments and directives erased.

    Args:
        text (bytes): A definition span of `function`.
        function (str): The function name.

    Returns:
        bytes | None: The whitespace-normalized prefix, or None if the name is not followed by `(`.
    """
    cleaned = _DIRECTIVE_LINE.sub(b"", _COMMENT.sub(b"", text))
    match = re.search(rb"\b" + re.escape(function.encode("utf-8")) + rb"\s*\(", cleaned)
    if match is None:
        return None
    return _normalize(cleaned[: match.start()])


def merge_function(
    *,
    canonical: bytes,
    snapshot: bytes,
    fork_base: bytes,
    function: str,
    frozen_functions: Collection[str] | None = None,
) -> tuple[bytes, MergeReport]:
    """Splice `function`'s definition and its new helpers from `snapshot` into `canonical`.

    `snapshot` is the session's copy of the file after the agent finished; `fork_base` is the
    canonical file as it was when that copy was taken, which tells the merge what the agent
    changed; `canonical` is the file as it is now, possibly already carrying other sessions'
    merges. Offsets in `canonical` are taken from a fresh parse, so earlier merges are accounted
    for; offsets in `snapshot` are independent of it.

    `frozen_functions` selects the isolation rule for edits to *other existing functions*. When
    it is None (the default) every such edit is dropped. Otherwise an edited function is carried
    -- its canonical definition is replaced by the session's, at its own span -- unless it is
    named in `frozen_functions` (a session is running on it), it has already changed in the
    canonical file since the fork (another session got there first), or its span cannot be found
    in both files. Carried functions are listed in `MergeReport.carried`; the caller re-verifies
    what they can affect before keeping the result. Edits to other existing items (macros,
    typedefs, declarations) are always dropped.

    Args:
        canonical (bytes): The current canonical file.
        snapshot (bytes): The session's finished copy.
        fork_base (bytes): The canonical file at fork time.
        function (str): The function the session worked on.
        frozen_functions (Collection[str] | None): Functions whose edits must not be carried
            because a session is running on them; None drops every edit to another function.

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
    # Both spans were accepted with parse errors tolerated, so make sure they are spans of the
    # same thing: a swallowed neighbour that the agent also edited would show up here.
    if not _same_prefix(snapshot, snapshot_span, canonical, canonical_span, function):
        return canonical, MergeReport(
            False,
            f"the text before {function}'s name differs between the session copy and the "
            "canonical file",
        )

    own_key = ("function", function)
    base_by_key = _index(iter_top_level_items(fork_base))
    canonical_by_key = _index(iter_top_level_items(canonical))
    snapshot_items = iter_top_level_items(snapshot)

    dropped: list[str] = []
    transplant: list[TopLevelItem] = []
    skipped: list[str] = []
    seen: set[tuple[str, str]] = set()
    carry_candidates: dict[str, TopLevelItem] = {}
    for item in snapshot_items:
        if item.key == own_key:
            continue
        seen.add(item.key)
        base_texts = base_by_key.get(item.key)
        if base_texts is not None:
            if _normalize(item.text) in base_texts:
                continue
            if frozen_functions is None or item.key[0] != "function":
                dropped.append(_render(item.key))
            elif item.key[1] in frozen_functions:
                dropped.append(f"{_render(item.key)} (a session is running on it)")
            elif canonical_by_key.get(item.key) != base_texts:
                dropped.append(
                    f"{_render(item.key)} (changed in the canonical file since the fork)"
                )
            else:
                carry_candidates.setdefault(item.key[1], item)
            continue
        canonical_texts = canonical_by_key.get(item.key)
        if canonical_texts is not None:
            if _normalize(item.text) in canonical_texts:
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

    prefix = b"".join(item.text.rstrip(b"\n") + b"\n\n" for item in transplant)
    replacements: list[tuple[int, int, bytes]] = [
        (
            canonical_span.start_byte,
            canonical_span.end_byte,
            prefix + snapshot[snapshot_span.start_byte : snapshot_span.end_byte],
        )
    ]
    carried: list[str] = []
    for name, item in carry_candidates.items():
        other_snapshot = find_function_span(snapshot, name)
        other_canonical = find_function_span(canonical, name)
        if (
            other_snapshot is None
            or other_canonical is None
            or not _same_prefix(snapshot, other_snapshot, canonical, other_canonical, name)
        ):
            dropped.append(f"{_render(item.key)} (its definition could not be matched)")
            continue
        replacements.append(
            (
                other_canonical.start_byte,
                other_canonical.end_byte,
                snapshot[other_snapshot.start_byte : other_snapshot.end_byte],
            )
        )
        carried.append(name)
    replacements.sort()
    for (_, previous_end, _), (start, _, _) in pairwise(replacements):
        if start < previous_end:
            return canonical, MergeReport(
                False, "the definitions to splice overlap in the canonical file", dropped=dropped
            )

    base_span = find_function_span(fork_base, function)
    body_changed = base_span is not None and _normalize(
        snapshot[snapshot_span.body_start_byte : snapshot_span.body_end_byte]
    ) != _normalize(fork_base[base_span.body_start_byte : base_span.body_end_byte])

    merged = canonical
    for start, end, text in reversed(replacements):
        merged = merged[:start] + text + merged[end:]
    return merged, MergeReport(
        True,
        transplanted=[_render(item.key) for item in transplant],
        skipped_duplicates=skipped,
        dropped=dropped,
        carried=carried,
        body_changed=body_changed,
    )


def _same_prefix(
    snapshot: bytes,
    snapshot_span: FunctionSpan,
    canonical: bytes,
    canonical_span: FunctionSpan,
    function: str,
) -> bool:
    """Return True iff the two spans carry the same text before `function`'s name.

    Args:
        snapshot (bytes): The session's copy.
        snapshot_span (FunctionSpan): `function`'s span in it.
        canonical (bytes): The canonical file.
        canonical_span (FunctionSpan): `function`'s span in it.
        function (str): The function name.

    Returns:
        bool: True iff the normalized, comment-free prefixes are identical.
    """
    return _signature_prefix(
        snapshot[snapshot_span.start_byte : snapshot_span.end_byte], function
    ) == _signature_prefix(canonical[canonical_span.start_byte : canonical_span.end_byte], function)


def _index(items: list[TopLevelItem]) -> dict[tuple[str, str], frozenset[bytes]]:
    """Return a map from item key to the normalized texts of every item with that key.

    A function or macro defined once per preprocessor branch has several texts under one key;
    indexing all of them is what lets an unchanged second definition compare as unchanged.

    Args:
        items (list[TopLevelItem]): Items in source order.

    Returns:
        dict[tuple[str, str], frozenset[bytes]]: Key to the set of normalized texts.
    """
    indexed: dict[tuple[str, str], set[bytes]] = {}
    for item in items:
        indexed.setdefault(item.key, set()).add(_normalize(item.text))
    return {key: frozenset(texts) for key, texts in indexed.items()}


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
