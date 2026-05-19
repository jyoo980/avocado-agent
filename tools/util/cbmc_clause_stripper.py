"""Strip CBMC contract clauses from C source before parsing it with tree-sitter.

CBMC's contract macros (`__CPROVER_requires`, `__CPROVER_ensures`,
`__CPROVER_assigns`, `__CPROVER_frees`) sit between a function's signature and
its body. Tree-sitter's C grammar cannot parse this, especially when a clause
contains `__CPROVER_forall { ... }` (the inner braces close the surrounding
`function_declarator` early). The fallout is that tree-sitter produces a single
`ERROR` node that swallows the affected function and every following function
in the file as siblings, which then forces every downstream consumer
(call graph, body extraction, clause enumeration) to recover by walking ERROR
subtrees — recovery that is fragile and has broken multiple times.

This module side-steps the parser problem entirely: it locates each top-level
CBMC clause via a small, comment- and string-aware scanner, then returns a
stripped copy of the source where every clause has been replaced with whitespace
of identical byte length (newlines preserved). The stripped source parses as
plain C, and every byte offset / line / column on the resulting tree-sitter AST
indexes the *original* source verbatim — no offset translation tables, no
two-buffer bookkeeping.

The same scanner also powers `find_cbmc_annotation_spans`, which locates *any*
`__CPROVER_*(...)` call — including in-body intrinsics like `__CPROVER_assume`
and `__CPROVER_assert` — so downstream consumers (e.g. the mutant generator)
can avoid mutating operators that live inside a CBMC annotation rather than in
the program under test.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
from dataclasses import dataclass

CBMC_CLAUSE_NAMES: tuple[str, ...] = (
    "__CPROVER_requires",
    "__CPROVER_ensures",
    "__CPROVER_assigns",
    "__CPROVER_frees",
)


@dataclass(frozen=True)
class CbmcClauseSpan:
    """One CBMC clause located in the original source.

    Attributes:
        kind: The clause macro name, e.g. ``__CPROVER_requires``.
        start_byte: Inclusive byte offset of the first character of the macro
            name in the original source.
        end_byte: Exclusive byte offset one past the closing ``)`` of the clause.
    """

    kind: str
    start_byte: int
    end_byte: int


def strip_cbmc_clauses(source: bytes) -> tuple[bytes, list[CbmcClauseSpan]]:
    """Return ``(stripped_source, spans)`` for the given C source bytes.

    ``stripped_source`` has the same length as ``source``. Every byte that lies
    inside a top-level CBMC clause is replaced with ``b" "``, except newline
    bytes which are preserved so that line numbers continue to match the
    original. Bytes outside any clause are returned unchanged.

    ``spans`` lists the located clauses in source order so callers can recover
    each clause's kind, text, and original byte range.

    Args:
        source: The original C source as bytes.

    Returns:
        A pair of (stripped bytes, list of CbmcClauseSpan).
    """
    clause_names = frozenset(CBMC_CLAUSE_NAMES)
    spans = _find_cbmc_call_spans(source, clause_names.__contains__)
    buffer = bytearray(source)
    for span in spans:
        for k in range(span.start_byte, span.end_byte):
            if buffer[k] != ord("\n"):
                buffer[k] = ord(" ")
    return bytes(buffer), spans


def find_cbmc_annotation_spans(source: bytes) -> list[CbmcClauseSpan]:
    """Return spans of every ``__CPROVER_*(...)`` call in ``source``.

    Covers both top-level contract clauses (requires/ensures/assigns/frees) and
    in-body intrinsics (assume/assert/havoc_object/...). Identifiers without a
    following ``(`` — e.g. ``__CPROVER_return_value`` — are skipped and do not
    appear in the result.

    Args:
        source: The original C source as bytes.

    Returns:
        Spans of CBMC annotation calls in source order.
    """
    return _find_cbmc_call_spans(source, lambda name: name.startswith("__CPROVER_"))


def _find_cbmc_call_spans(
    source: bytes, name_predicate: Callable[[str], bool]
) -> list[CbmcClauseSpan]:
    """Locate every ``identifier(...)`` call whose identifier satisfies ``name_predicate``.

    Skips C comments and string/character literals so identifiers inside them
    are not matched. For each identifier-start boundary in ``source``, the
    identifier is read in full and passed to ``name_predicate``; on a match,
    ``_consume_clause`` finds the matching ``)`` (tracking paren and brace
    depth independently so a ``__CPROVER_forall { ... }`` inside the call
    doesn't close it early). Identifiers without a following ``(`` are skipped.

    Args:
        source: The original C source as bytes.
        name_predicate: Predicate over identifier names.

    Returns:
        Matching spans in source order.
    """
    spans: list[CbmcClauseSpan] = []
    length = len(source)
    i = 0
    while i < length:
        ch = source[i]
        if ch == ord("/") and i + 1 < length and source[i + 1] == ord("/"):
            i += 2
            while i < length and source[i] != ord("\n"):
                i += 1
            continue
        if ch == ord("/") and i + 1 < length and source[i + 1] == ord("*"):
            i += 2
            while i + 1 < length and not (source[i] == ord("*") and source[i + 1] == ord("/")):
                i += 1
            i += 2
            continue
        if ch == ord('"'):
            i = _skip_literal(source, i, ord('"'))
            continue
        if ch == ord("'"):
            i = _skip_literal(source, i, ord("'"))
            continue
        if _is_identifier_byte(ch) and _identifier_starts_at(source, i):
            end = _skip_identifier(source, i)
            name = source[i:end].decode("ascii", errors="replace")
            if name_predicate(name):
                span_end = _consume_clause(source, end)
                if span_end is not None:
                    spans.append(CbmcClauseSpan(kind=name, start_byte=i, end_byte=span_end))
                    i = span_end
                    continue
            i = end
            continue
        i += 1
    return spans


def _is_identifier_byte(b: int) -> bool:
    """Return True iff ``b`` can appear inside a C identifier.

    Args:
        b: A single byte.

    Returns:
        True iff ``b`` is ``[A-Za-z0-9_]``.
    """
    return (
        b == ord("_")
        or ord("0") <= b <= ord("9")
        or ord("A") <= b <= ord("Z")
        or ord("a") <= b <= ord("z")
    )


def _identifier_starts_at(source: bytes, i: int) -> bool:
    """Return True iff ``i`` is at an identifier-start boundary in ``source``.

    Args:
        source: The source bytes.
        i: A candidate identifier-start offset.

    Returns:
        True iff ``source[i]`` begins a fresh identifier (preceded by a
        non-identifier byte or by start-of-buffer).
    """
    if i == 0:
        return True
    return not _is_identifier_byte(source[i - 1])


def _skip_identifier(source: bytes, i: int) -> int:
    """Return the byte offset just past the identifier starting at ``i``.

    Args:
        source: The source bytes.
        i: Identifier-start offset.

    Returns:
        The first offset at or after ``i`` whose byte is not an identifier byte.
    """
    length = len(source)
    j = i
    while j < length and _is_identifier_byte(source[j]):
        j += 1
    return j


def _skip_literal(source: bytes, i: int, quote: int) -> int:
    """Return the byte offset just past the string/character literal at ``i``.

    Args:
        source: The source bytes.
        i: Offset of the opening quote byte.
        quote: The opening quote byte (``"`` or ``'``).

    Returns:
        The offset of the byte immediately after the closing quote.
    """
    length = len(source)
    j = i + 1
    while j < length:
        b = source[j]
        if b == ord("\\") and j + 1 < length:
            j += 2
            continue
        if b == quote:
            return j + 1
        j += 1
    return length


def _consume_clause(source: bytes, i: int) -> int | None:
    """Return the byte offset just past a clause that opens at or near ``i``.

    The macro name has already been matched; ``i`` indexes the byte immediately
    after the name. Skip whitespace, expect ``(``, then walk to the matching
    ``)`` while tracking paren depth and brace depth independently. Comments
    and string/character literals inside the clause are skipped so a ``)``
    inside them does not close the clause early.

    Args:
        source: The source bytes.
        i: The byte offset immediately after the matched clause-macro name.

    Returns:
        The offset one past the closing ``)``, or ``None`` if no opening ``(``
        follows or the file ends before the clause is closed.
    """
    length = len(source)
    j = i
    while j < length and source[j] in (ord(" "), ord("\t"), ord("\n"), ord("\r")):
        j += 1
    if j >= length or source[j] != ord("("):
        return None
    paren_depth = 0
    brace_depth = 0
    while j < length:
        b = source[j]
        if b == ord("/") and j + 1 < length and source[j + 1] == ord("/"):
            j += 2
            while j < length and source[j] != ord("\n"):
                j += 1
            continue
        if b == ord("/") and j + 1 < length and source[j + 1] == ord("*"):
            j += 2
            while j + 1 < length and not (source[j] == ord("*") and source[j + 1] == ord("/")):
                j += 1
            j += 2
            continue
        if b == ord('"'):
            j = _skip_literal(source, j, ord('"'))
            continue
        if b == ord("'"):
            j = _skip_literal(source, j, ord("'"))
            continue
        if b == ord("("):
            paren_depth += 1
        elif b == ord(")"):
            paren_depth -= 1
            if paren_depth == 0 and brace_depth == 0:
                return j + 1
        elif b == ord("{"):
            brace_depth += 1
        elif b == ord("}"):
            brace_depth -= 1
        j += 1
    return None
