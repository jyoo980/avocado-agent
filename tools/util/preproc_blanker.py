"""Blank preprocessor conditionals that break tree-sitter's C grammar.

Tree-sitter parses raw, unpreprocessed C. Two preprocessor idioms that are common in real
code make it produce a badly broken AST:

1. Conditional directives *inside a function body* that wrap a label or split a statement,
   e.g. ``#if FAST / label: / #endif``. The ``labeled_statement`` swallows the ``#endif`` line,
   the enclosing ``preproc_if`` then runs to end-of-file, and the function is no longer a
   ``function_definition`` at all.
2. ``#if 0`` blocks, whose contents tree-sitter parses as live code, so calls in dead code show up
   in the call graph.

This module erases exactly those pieces with whitespace of identical byte length (newlines
preserved), mirroring `cbmc_clause_stripper`: every byte offset, line and column on the resulting
AST still indexes the original source verbatim.

- Conditional directive lines (``#if``/``#ifdef``/``#ifndef``/``#elif``/``#else``/``#endif``) at
  brace depth > 0 are blanked; their branch contents are kept, so the parser sees the union of all
  branches. Top-level conditional directives are left alone since tree-sitter handles them.
- The contents of an ``#if 0`` branch are blanked up to its matching ``#elif``/``#else``/``#endif``.
- A directive extends over backslash-newline continuations, so a multi-line ``#define`` (or
  ``#if``) is treated as a single logical line: braces on its continuation lines don't count, and
  a blanked directive is blanked in full.

Which branch of an unknown condition a compiler would take cannot be decided without
preprocessing, so nothing else is touched.

This module results in the code from which the call-graph determining verification order is
created. Verification always happens on the unmodified file.
"""

from __future__ import annotations

import re

from .cbmc_clause_stripper import _skip_literal

_CONDITIONAL_DIRECTIVE = re.compile(
    rb"[ \t]*#[ \t]*(if|ifdef|ifndef|elif|else|endif)\b(.*)", re.DOTALL
)
_OPENING_DIRECTIVES = (b"if", b"ifdef", b"ifndef")
# `0`, `(0)`, `0 /* comment */`, `0 // comment`.
_LITERAL_ZERO = re.compile(rb"\(*\s*0\s*\)*(\s*(/[/*].*)?)?", re.DOTALL)

_NEWLINE = ord("\n")
_BACKSLASH = ord("\\")
_SPACE = ord(" ")
_SLASH = ord("/")
_STAR = ord("*")
_OPEN_BRACE = ord("{")
_CLOSE_BRACE = ord("}")


def blank_preprocessor_conditionals(source: bytes) -> bytes:
    """Return ``source`` with problematic preprocessor conditionals replaced by whitespace.

    The result has the same length as ``source`` and newlines at the same offsets.

    Args:
        source: The C source as bytes (typically already passed through `strip_cbmc_clauses`).

    Returns:
        The blanked source bytes.
    """
    buffer = bytearray(source)
    length = len(source)
    brace_depth = 0
    dead_branch_start: int | None = None  # Offset just past the `#if 0` line while inside one.
    dead_nesting = 0  # Nested `#if*` groups opened inside the dead branch.
    i = 0
    while i < length:
        at_line_start = i == 0 or source[i - 1] == _NEWLINE
        if at_line_start:
            line_end = _logical_line_end(source, i)
            match = _CONDITIONAL_DIRECTIVE.fullmatch(source, i, line_end)
            if match:
                kind = match.group(1)
                argument = match.group(2).replace(b"\\\n", b" ").strip()
                if dead_branch_start is not None:
                    if kind in _OPENING_DIRECTIVES:
                        dead_nesting += 1
                    elif kind == b"endif" and dead_nesting > 0:
                        dead_nesting -= 1
                    elif dead_nesting == 0:
                        # `#elif`/`#else`/`#endif` closing the dead branch.
                        _blank(buffer, dead_branch_start, i)
                        dead_branch_start = None
                        if brace_depth > 0:
                            _blank(buffer, i, line_end)
                elif kind == b"if" and _LITERAL_ZERO.fullmatch(argument):
                    dead_branch_start = min(line_end + 1, length)
                    dead_nesting = 0
                    if brace_depth > 0:
                        _blank(buffer, i, line_end)
                elif brace_depth > 0:
                    _blank(buffer, i, line_end)
                i = line_end + 1
                continue
            if source[i:line_end].lstrip().startswith(b"#"):
                # Any other directive (`#define`, `#include`, ...): braces on it don't count.
                i = line_end + 1
                continue
        if dead_branch_start is not None:
            # Dead code: only look for the directive that ends it.
            i += 1
            continue
        byte = source[i]
        if byte == _SLASH and i + 1 < length and source[i + 1] == _SLASH:
            i += 2
            while i < length and source[i] != _NEWLINE:
                i += 1
            continue
        if byte == _SLASH and i + 1 < length and source[i + 1] == _STAR:
            i += 2
            while i + 1 < length and not (source[i] == _STAR and source[i + 1] == _SLASH):
                i += 1
            i += 2
            continue
        if byte == ord('"') or byte == ord("'"):
            i = _skip_literal(source, i, byte)
            continue
        if byte == _OPEN_BRACE:
            brace_depth += 1
        elif byte == _CLOSE_BRACE:
            brace_depth -= 1
        i += 1
    if dead_branch_start is not None:
        # Unterminated `#if 0`: treat the rest of the file as dead.
        _blank(buffer, dead_branch_start, length)
    result = bytes(buffer)
    assert len(result) == len(source), "blanking must preserve length"
    return result


def _logical_line_end(source: bytes, start: int) -> int:
    """Return the offset of the newline ending the logical line starting at ``start``.

    A physical line whose last byte before the newline is a backslash continues onto the next
    physical line, as in a multi-line macro definition.

    Args:
        source: The C source as bytes.
        start: Offset of the first byte of the line.

    Returns:
        The offset of the terminating newline, or ``len(source)`` if there is none.
    """
    length = len(source)
    i = start
    while True:
        line_end = source.find(b"\n", i)
        if line_end == -1:
            return length
        if line_end > start and source[line_end - 1] == _BACKSLASH:
            i = line_end + 1
            continue
        return line_end


def _blank(buffer: bytearray, start: int, end: int) -> None:
    """Replace ``buffer[start:end]`` with spaces, keeping newlines.

    Args:
        buffer: The buffer to modify in place.
        start: Inclusive start offset.
        end: Exclusive end offset.
    """
    for k in range(start, end):
        if buffer[k] != _NEWLINE:
            buffer[k] = _SPACE
