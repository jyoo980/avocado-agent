"""Decide whether a session's local edits to a C file are ones the merge may keep.

A session's job is to add specifications. check_edits` compares the session's finished copy (the
*snapshot*) with the file as it was when it was forked. Returns one `Violation` per broken rule:

1. Original code is unchanged: every original top-level item is still present, in the same order,
   with the same text. Whitespace, comments and contract clauses do not count.
2. Insertions go in two places only: the contract slot of a function, or file scope. Nothing is
   inserted inside an existing item, and no contract clause sits inside a function body.
3. The session's function is intact: it is defined as many times as it was when the session
   started (once, unless it is defined per preprocessor branch).
4. New names carry the reserved prefix and do not already exist in the file. No new `#undef`,
   `#pragma` or `#include`, and a new `#if` block may enclose only new items.
5. No `__CPROVER_assume` in new text.
8. No literally false precondition.

Rules 6 (only the source file is edited), 7 (the file compiles) and 9 (no verified function stops
verifying, where a vacuous pass does not count as verifying) need the file system or CBMC and are
checked by the caller, `avocado_verify._merge_and_verify`. Rule 10 (no merge conflict) is decided
by `merge_copy` below, which the caller also uses: an edit to another function's contract is left
out when another live session is working on that function or when its contract changed in the
shared file since the fork, and nothing merges when a new name is already defined differently
there.

`merge_copy` then produces the file to commit, reconciling the copy with whatever other sessions
merged since it was taken, and returns a `MergeReport` for the run log. Because every version is
the same original code plus contracts plus new items, that is decided item by item and no function
has to be located or parsed. `frozen_functions_for` and `functions_affected_by` give the merge's
callers the two sets that rules 10 and 9 are about: the functions whose contracts a session may not
change, and the functions a kept change can break.

The comparison deliberately does not use tree-sitter. Contract clauses and comments are erased,
both files are split into tokens and then into top-level items by brace depth alone, and the two
item sequences are aligned. That is robust to constructs the C grammar cannot parse
(`__CPROVER_forall { ... }` in a helper, attribute macros, operator macros), which made an AST
comparison reject merges that had changed no code.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from operator import itemgetter
from typing import TYPE_CHECKING

from tools.util.cbmc_clause_stripper import strip_cbmc_clauses

if TYPE_CHECKING:
    from collections.abc import Collection

    from tools.util.callgraph import CallGraph

# The prefixes a new top-level name must start with: the first for macros, the second for
# functions, variables and types. Either is accepted for any kind of name; what matters is that no
# existing code can already use, or be made to use, a name that starts with one of them.
RESERVED_PREFIXES = ("AVOCADO_SPEC_", "avocado_spec_")

# One-line titles of the rules, used when a violation is rendered.
RULE_TITLES = {
    1: "original code is unchanged",
    2: "insertions go in the contract slot or at file scope only",
    3: "the session's function is intact",
    4: "new names carry the reserved prefix",
    5: "no __CPROVER_assume in new text",
    6: "only the source file is edited",
    7: "the file compiles",
    8: "no literally false preconditions",
    9: "no broken verified specs",
    10: "no merge conflict",
}

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
_ASSUME = re.compile(r"\b__CPROVER_assume\b")

_CONDITIONAL_OPENERS = frozenset({"if", "ifdef", "ifndef"})
_CONDITIONAL_BRANCHES = frozenset({"elif", "else"})
_AGGREGATE_KEYWORDS = frozenset({"struct", "union", "enum"})

# `__CPROVER_requires` arguments that are false whatever the inputs, with whitespace, comments and
# redundant outer parentheses removed.
_FALSE_PRECONDITIONS = frozenset(
    {"0", "0u", "0U", "0l", "0L", "false", "FALSE", "!1", "!true", "1==0", "0==1", "0!=0", "1!=1"}
)

# How many tokens of context a message quotes.
_QUOTED_TOKENS = 6


@dataclass(frozen=True)
class Violation:
    """One broken rule.

    Attributes:
        rule (int): The rule's number, as in `RULE_TITLES` and CLAUDE.md.
        message (str): What is wrong, in words the agent can act on.
        line (int | None): The line of the session's copy the violation is at, when it has one.
    """

    rule: int
    message: str
    line: int | None = None

    def __str__(self) -> str:
        """Return the line printed for this violation.

        Returns:
            str: `rule N (title): line L: message`.
        """
        where = f"line {self.line}: " if self.line is not None else ""
        return f"rule {self.rule} ({RULE_TITLES[self.rule]}): {where}{self.message}"


@dataclass(frozen=True)
class MergeReport:
    """What a merge kept, what it left out, and why it failed, for the run log.

    Attributes:
        merged (bool): True iff the canonical file was updated.
        reason (str): Why the merge failed; empty on success.
        transplanted (list[str]): New top-level items carried into the canonical file, rendered
            as `kind:name`.
        skipped_duplicates (list[str]): New items that another session had already added with
            identical text, so nothing needed doing.
        dropped (list[str]): The session's edits to the contracts of other functions that the
            merge left out (rule 10, or rule 9 when the harness reverts them), rendered as
            `function:name` with the reason in parentheses when there is a specific one.
        carried (list[str]): Other functions whose contracts the session changed and the merge
            kept; the caller re-verifies what they can affect before committing.
        changed_since_fork (list[str]): Functions whose contracts differ between the file the
            session's copy was taken from and the canonical file the session's function was last
            verified on, because other sessions merged meanwhile. The session never saw those
            contracts, so a function that calls one of them and then fails its ground truth is
            given another session (see `avocado_verify._note_for_rerun_after_merge`).
        violations (list[str]): The edit rules the session's copy broke, one rendered line each;
            non-empty only when they are why the merge failed.
    """

    merged: bool
    reason: str = ""
    transplanted: list[str] = field(default_factory=list)
    skipped_duplicates: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    carried: list[str] = field(default_factory=list)
    changed_since_fork: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)

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
            "changed_since_fork": list(self.changed_since_fork),
            "violations": list(self.violations),
        }


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


def check_edits(base: bytes, snapshot: bytes, function: str) -> list[Violation]:
    """Return the rules a session's copy breaks, in the order of the lines they are at.

    Args:
        base (bytes): The file as it was when the session's copy was taken.
        snapshot (bytes): The session's finished copy.
        function (str): The function the session works on.

    Returns:
        list[Violation]: One entry per violation of rules 1-5 and 8; empty when the copy is clean.
    """
    comparison = _compare(base, snapshot)
    violations = list(comparison.violations)
    violations += _check_clause_placement(snapshot, comparison.snapshot_tokens)
    violations += _check_function_intact(comparison.base_items, comparison.snapshot_items, function)
    violations += _check_new_items(
        comparison.base_items, comparison.snapshot_items, comparison.new_indices
    )
    violations += _check_preconditions(base, snapshot)
    return sorted(violations, key=lambda violation: (violation.line or 0, violation.rule))


def merge_copy(
    *,
    canonical: bytes,
    snapshot: bytes,
    fork_base: bytes,
    function: str,
    frozen_functions: Collection[str] | None = None,
) -> tuple[bytes, MergeReport]:
    """Return the file to commit for a session whose copy passed `check_edits`.

    Every version of the file is the same original code plus contracts on some items plus new,
    prefixed items, so the merge is decided item by item, with no function to locate or parse.
    Three versions are reconciled: the fork base, the session's copy (*mine*) and the canonical
    file as it is now (*theirs*), which other sessions may have merged into since the fork.

    - The session's own function always takes its contract from the copy.
    - A contract the session changed on *another* item is kept, and the item named in
      `MergeReport.carried` for the caller to re-verify, unless `frozen_functions` is None (no
      such edit is kept), the item is frozen (a live session is working on it), or the
      item's contract also changed in the canonical file since the fork. In those cases the
      edit is left out and named in `MergeReport.dropped`; the rest of the copy still merges.
    - Items the session added are inserted after the original item they followed in the copy.
      One that the canonical file already has, with the same text, is skipped; one whose name
      the canonical file now defines differently fails the merge.

    When the canonical file is still the fork base -- always the case with one session at a time
    -- this reduces to taking the copy itself, minus the edits that are left out.

    The session's own contract is never left out silently: the merge fails, and says why, when
    the session's function cannot be found as a function definition in the copy, or when the
    copy changes the contract of an item the merge cannot name. A session can only edit contract
    slots, so such an item is a function definition the parser could not make out, and it may be
    the session's own.

    `MergeReport.changed_since_fork` names every function whose contract changed in the canonical
    file since the fork, whether or not the session edited it too.

    Args:
        canonical (bytes): The current canonical file.
        snapshot (bytes): The session's finished copy.
        fork_base (bytes): The canonical file when the copy was taken.
        function (str): The function the session worked on.
        frozen_functions (Collection[str] | None): Functions whose contracts this session may
            not change; None means it may change no other function's contract.

    Returns:
        tuple[bytes, MergeReport]: The merged file (or `canonical` unchanged on failure) and the
            report of what was kept and dropped.
    """
    mine = _compare(fork_base, snapshot)
    if mine.violations:
        rendered = [str(violation) for violation in mine.violations]
        return canonical, MergeReport(
            False, f"the session copy breaks {len(rendered)} edit rule(s)", violations=rendered
        )
    if not any(item.kind == "function" and item.name == function for item in mine.snapshot_items):
        return canonical, MergeReport(
            False,
            f"{function} could not be found as a function definition in the session copy, so its "
            "contract could not be merged; nothing was merged",
        )
    theirs = mine if canonical == fork_base else _compare(fork_base, canonical)
    if canonical != fork_base and theirs.violations:
        return canonical, MergeReport(
            False,
            "the code of the canonical file changed since the copy was taken: "
            f"{theirs.violations[0].message}",
        )
    theirs_source = fork_base if canonical == fork_base else canonical
    theirs_items = mine.base_items if canonical == fork_base else theirs.snapshot_items
    theirs_index = (
        {index: index for index in range(len(mine.base_items))}
        if canonical == fork_base
        else dict(theirs.pairs)
    )

    carried: list[str] = []
    dropped: list[str] = []
    changed_since_fork: list[str] = []
    edits: list[tuple[int, int, bytes]] = []  # (start, end, replacement) in `theirs_source`
    mine_to_theirs: dict[int, _Item] = {}
    for base_index, snapshot_index in mine.pairs:
        original = mine.base_items[base_index]
        edited = mine.snapshot_items[snapshot_index]
        current = theirs_items[theirs_index[base_index]]
        mine_to_theirs[snapshot_index] = current
        if edited.kind == "directive":
            continue
        base_text = _code_text(fork_base[original.start : original.end])
        my_text = _code_text(snapshot[edited.start : edited.end])
        their_text = _code_text(theirs_source[current.start : current.end])
        if (
            their_text != base_text
            and current.kind == "function"
            and current.name
            and current.name not in changed_since_fork
        ):
            changed_since_fork.append(current.name)
        if my_text in (base_text, their_text):
            continue
        if edited.name is None:
            return canonical, MergeReport(
                False,
                f"the copy changes the contract of an item at line {edited.line} that the merge "
                f"cannot name, so it cannot tell whether that is the contract of {function}; "
                "nothing was merged",
                dropped=dropped,
            )
        if edited.name != function:
            reason = _reason_not_carried(
                edited.name, frozen_functions, their_changed=their_text != base_text
            )
            if reason is not None:
                dropped.append(f"function:{edited.name}{reason}")
                continue
            if edited.name not in carried:
                carried.append(edited.name)
        edits.append((current.start, current.end, snapshot[edited.start : edited.end]))

    their_new = {} if canonical == fork_base else _named_items(theirs, theirs.new_indices)
    transplanted: list[str] = []
    skipped: list[str] = []
    for run in _runs(mine.new_indices):
        kept = []
        for index in run:
            item = mine.snapshot_items[index]
            existing = their_new.get((item.kind, item.directive, item.name)) if item.name else None
            if existing is None:
                kept.append(index)
                transplanted.append(_render_new_item(item))
            elif _code_text(canonical[existing.start : existing.end]) == _code_text(
                snapshot[item.start : item.end]
            ):
                skipped.append(_render_new_item(item))
            else:
                return canonical, MergeReport(
                    False,
                    f"{_render_new_item(item)} is already defined differently in the canonical "
                    "file",
                    dropped=dropped,
                )
        if not kept:
            continue
        anchor = mine_to_theirs.get(run[0] - 1)
        position = anchor.end if anchor is not None else 0
        preceding = mine.snapshot_items[run[0] - 1].end if run[0] > 0 else 0
        if kept == run:
            text = snapshot[preceding : mine.snapshot_items[run[-1]].end]
        else:
            text = b"".join(
                b"\n" + snapshot[mine.snapshot_items[index].start : mine.snapshot_items[index].end]
                for index in kept
            )
        edits.append((position, position, text if anchor is not None else text + b"\n"))

    merged = theirs_source
    for start, end, text in sorted(edits, key=itemgetter(0, 1), reverse=True):
        merged = merged[:start] + text + merged[end:]
    if canonical != fork_base and _compare(canonical, merged).violations:
        return canonical, MergeReport(
            False, "the merge would change code of the canonical file; nothing was merged"
        )
    return merged, MergeReport(
        True,
        transplanted=transplanted,
        skipped_duplicates=skipped,
        dropped=dropped,
        carried=carried,
        changed_since_fork=changed_since_fork,
    )


def functions_with_changed_contracts(before: bytes, after: bytes) -> list[str]:
    """Return the functions whose contracts differ between two versions of a file.

    Both versions must hold the same code, as any two versions of the canonical file do. A
    function that only one of them has is not reported.

    Args:
        before (bytes): One version of the file.
        after (bytes): Another version of it.

    Returns:
        list[str]: The names of the functions, in source order.
    """
    if before == after:
        return []
    comparison = _compare(before, after)
    changed: list[str] = []
    for before_index, after_index in comparison.pairs:
        old = comparison.base_items[before_index]
        new = comparison.snapshot_items[after_index]
        if old.kind != "function" or not old.name or old.name in changed:
            continue
        if _code_text(before[old.start : old.end]) != _code_text(after[new.start : new.end]):
            changed.append(old.name)
    return changed


def function_header(source: bytes, function: str) -> bytes | None:
    """Return a function definition's text before its body: its signature and its contract.

    Args:
        source (bytes): A C source.
        function (str): The function's name.

    Returns:
        bytes | None: The text from the start of the first definition of `function` up to the
            opening brace of its body, or None when the definition is not found.
    """
    tokens = _tokenize(source)
    for item in _split_items(tokens):
        if item.kind != "function" or item.name != function:
            continue
        for token in tokens:
            if token.offset >= item.start and token.depth == 0 and token.text == "{":
                return source[item.start : token.offset].rstrip()
    return None


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


def _reason_not_carried(
    name: str | None, frozen_functions: Collection[str] | None, *, their_changed: bool
) -> str | None:
    """Return why a contract edit to another item is left out of a merge, or None to keep it.

    Args:
        name (str | None): The item's name, when it can be told.
        frozen_functions (Collection[str] | None): Functions the session may not change; None
            means it may change no other function.
        their_changed (bool): True iff the item's contract changed in the canonical file since
            the fork.

    Returns:
        str | None: A parenthesized reason to append to the item's label ("" when the caller
            simply allows no such edit), or None when the edit is kept.
    """
    if frozen_functions is None or name is None:
        return ""
    if name in frozen_functions:
        return " (a live session is working on it)"
    if their_changed:
        return " (changed in the canonical file since the fork)"
    return None


def functions_affected_by(
    carried: Collection[str],
    *,
    call_graph: CallGraph | None,
    candidates: Collection[str],
    exclude: str,
) -> list[str]:
    """Return the functions whose ground truth a merge carrying edits to `carried` can change.

    Those are the carried functions themselves (their contracts changed) and every in-file caller
    of one among `candidates` (a caller is verified against its callees' contracts). `exclude` is
    the merging function, which is verified separately.

    Args:
        carried (Collection[str]): Functions whose edited definitions the merge carries.
        call_graph (CallGraph | None): Call graph of the file; None yields no callers.
        candidates (Collection[str]): The functions whose verdicts a change to a callee can make
            stale (`tools.util.session_state.rechecked_on_change`): those verified on the
            canonical file and those whose proof is vacuous.
        exclude (str): The function whose session is merging.

    Returns:
        list[str]: The affected functions, sorted.
    """
    affected = set(carried)
    if call_graph is not None:
        affected.update(
            caller
            for caller in candidates
            if caller in call_graph
            and any(other in call_graph.get_callees(caller).internal for other in carried)
        )
    affected.discard(exclude)
    return sorted(affected)


def frozen_functions_for(function: str, active: Collection[str]) -> set[str]:
    """Return the functions whose contracts the session for `function` may not change.

    Those are the functions other live sessions are working on: each is its session's to
    specify, and that session's copy replaces whatever contract it has when it merges. The
    functions a live session *calls* are not frozen. Any session may fix a callee's contract;
    a live caller that then fails against the new contract is given another session on the file
    as it now is (see `avocado_verify._note_for_rerun_after_merge`).

    Args:
        function (str): The function whose session is merging.
        active (Collection[str]): Functions with a live session, `function` included or not.

    Returns:
        set[str]: The frozen functions.
    """
    return {name for name in active if name != function}


def _named_items(comparison: _Comparison, indices: list[int]) -> dict[tuple, _Item]:
    """Index the named items among `indices` of a comparison's snapshot by kind and name.

    Args:
        comparison (_Comparison): A comparison.
        indices (list[int]): Indices into its `snapshot_items`.

    Returns:
        dict[tuple, _Item]: `(kind, directive, name)` to the first such item.
    """
    named: dict[tuple, _Item] = {}
    for index in indices:
        item = comparison.snapshot_items[index]
        if item.name:
            named.setdefault((item.kind, item.directive, item.name), item)
    return named


def _runs(indices: list[int]) -> list[list[int]]:
    """Group sorted indices into runs of consecutive values.

    Args:
        indices (list[int]): Sorted, distinct indices.

    Returns:
        list[list[int]]: The maximal runs, in order.
    """
    runs: list[list[int]] = []
    for index in indices:
        if runs and runs[-1][-1] == index - 1:
            runs[-1].append(index)
        else:
            runs.append([index])
    return runs


def function_line(source: bytes, function: str) -> int | None:
    """Return the line a function's definition starts on, without parsing the file.

    Args:
        source (bytes): A C source.
        function (str): The function's name.

    Returns:
        int | None: The line of its first definition, or None when it is not found.
    """
    for item in _split_items(_tokenize(source)):
        if item.kind == "function" and item.name == function:
            return item.line
    return None


@dataclass(frozen=True)
class _Comparison:
    """The base and the snapshot split into items and aligned.

    Attributes:
        base_items (list[_Item]): The base's items.
        snapshot_items (list[_Item]): The snapshot's items.
        snapshot_tokens (list[_Token]): The snapshot's tokens.
        violations (list[Violation]): The rule 1 and rule 2 violations the alignment shows.
        new_indices (list[int]): Indices into `snapshot_items` of the items the session added.
        pairs (list[tuple[int, int]]): For each original item still present with the same code,
            its index in `base_items` and in `snapshot_items`.
    """

    base_items: list[_Item]
    snapshot_items: list[_Item]
    snapshot_tokens: list[_Token]
    violations: list[Violation] = field(default_factory=list)
    new_indices: list[int] = field(default_factory=list)
    pairs: list[tuple[int, int]] = field(default_factory=list)


def _compare(base: bytes, snapshot: bytes) -> _Comparison:
    """Split both files into items and align them.

    Args:
        base (bytes): The file as it was when the session's copy was taken.
        snapshot (bytes): The session's finished copy.

    Returns:
        _Comparison: The items, their alignment, and the rule 1 and 2 violations it shows.
    """
    snapshot_tokens = _tokenize(snapshot)
    base_items = _split_items(_tokenize(base))
    snapshot_items = _split_items(snapshot_tokens)
    violations, new_indices, pairs = _compare_items(base_items, snapshot_items)
    return _Comparison(base_items, snapshot_items, snapshot_tokens, violations, new_indices, pairs)


def _code_text(raw: bytes) -> bytes:
    """Return an item's text with comments removed and whitespace collapsed.

    Unlike an item's tokens this keeps contract clauses, so two items with equal tokens and
    different `_code_text` differ only in their contracts.

    Args:
        raw (bytes): The item's bytes.

    Returns:
        bytes: The normalized text.
    """
    return b" ".join(_COMMENT.sub(" ", raw.decode("latin-1")).encode("latin-1").split())


def _render_new_item(item: _Item) -> str:
    """Return a short `kind:name` form of an added item for the run log.

    Args:
        item (_Item): An item the session added.

    Returns:
        str: For example `function:avocado_spec_ok` or `macro:AVOCADO_SPEC_MAX`.
    """
    if item.kind == "directive":
        category = "macro" if item.directive == "define" else item.directive
    else:
        category = item.kind
    return f"{category}:{item.name or _quote(item.tokens)}"


def stray_file_violations(stray_files: list[str]) -> list[Violation]:
    """Return the rule 6 violations for files added or changed beside the source file.

    Args:
        stray_files (list[str]): Relative paths tagged `added:` or `modified:`, as returned by
            `tools.util.session_state.find_stray_files`.

    Returns:
        list[Violation]: One violation per file.
    """
    violations = []
    for entry in stray_files:
        change, _, path = entry.partition(":")
        if change == "added":
            message = f"{path} was added to the copied directory; delete it or move it outside"
        else:
            message = f"{path} was modified; restore it, only the source file may be edited"
        violations.append(Violation(6, message))
    return violations


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


def _compare_items(
    base_items: list[_Item], snapshot_items: list[_Item]
) -> tuple[list[Violation], list[int], list[tuple[int, int]]]:
    """Check rules 1 and 2 on the aligned items, and say which snapshot items are new.

    Args:
        base_items (list[_Item]): The base's items.
        snapshot_items (list[_Item]): The snapshot's items.

    Returns:
        tuple[list[Violation], list[int], list[tuple[int, int]]]: The violations, the indices
            into `snapshot_items` of the items the session added, and the index pairs of the
            items both files share.
    """
    matcher = SequenceMatcher(
        None,
        [item.tokens for item in base_items],
        [item.tokens for item in snapshot_items],
        autojunk=False,
    )
    violations: list[Violation] = []
    new_indices: list[int] = []
    missing: list[_Item] = []
    pairs: list[tuple[int, int]] = []
    for tag, base_start, base_end, snapshot_start, snapshot_end in matcher.get_opcodes():
        if tag == "equal":
            pairs += zip(
                range(base_start, base_end), range(snapshot_start, snapshot_end), strict=True
            )
            continue
        added = list(range(snapshot_start, snapshot_end))
        for original in base_items[base_start:base_end]:
            counterpart = _find_counterpart(original, snapshot_items, added)
            if counterpart is None:
                missing.append(original)
                continue
            added.remove(counterpart)
            violations.append(_describe_change(original, snapshot_items[counterpart]))
        new_indices += added
    for original in missing:
        moved = next(
            (index for index in new_indices if snapshot_items[index].tokens == original.tokens),
            None,
        )
        if moved is not None:
            new_indices.remove(moved)
            violations.append(
                Violation(
                    1,
                    f"{original.describe()} was moved; original code must stay in its place",
                    snapshot_items[moved].line,
                )
            )
        else:
            violations.append(
                Violation(
                    1,
                    f"{original.describe()} (line {original.line} of the file as it was when "
                    "the session started) was removed or renamed; restore it",
                )
            )
    return violations, sorted(new_indices), pairs


def _find_counterpart(original: _Item, snapshot_items: list[_Item], added: list[int]) -> int | None:
    """Return the snapshot item that is an edited version of `original`, if there is one.

    Args:
        original (_Item): A base item that has no identical item in the snapshot.
        snapshot_items (list[_Item]): The snapshot's items.
        added (list[int]): Indices of the snapshot items not matched to any base item, in the
            same region of the file.

    Returns:
        int | None: The index of the first such item of the same kind and name, or None.
    """
    if original.name is None:
        return None
    for index in added:
        candidate = snapshot_items[index]
        same_kind = candidate.kind == original.kind and candidate.directive == original.directive
        if same_kind and candidate.name == original.name:
            return index
    return None


def _describe_change(original: _Item, edited: _Item) -> Violation:
    """Return the violation for an original item whose text differs in the snapshot.

    Args:
        original (_Item): The item in the base.
        edited (_Item): The same item in the snapshot.

    Returns:
        Violation: Rule 2 when text was only inserted into the item, rule 1 otherwise.
    """
    index = 0
    shared = min(len(original.tokens), len(edited.tokens))
    while index < shared and original.tokens[index] == edited.tokens[index]:
        index += 1
    line = edited.lines[min(index, len(edited.lines) - 1)]
    found = _quote(edited.tokens[index:])
    if _is_subsequence(original.tokens, edited.tokens):
        return Violation(
            2,
            f"text was inserted inside {original.describe()}, starting at `{found}`; the only "
            "thing that may be added to an existing item is a contract clause between a "
            "function's signature and its opening brace",
            line,
        )
    expected = _quote(original.tokens[index:])
    return Violation(
        1,
        f"the code of {original.describe()} changed: found `{found}` where the original has "
        f"`{expected}`; restore the original text",
        line,
    )


def _is_subsequence(shorter: tuple[str, ...], longer: tuple[str, ...]) -> bool:
    """Return True iff `shorter` can be obtained from `longer` by deleting tokens.

    Args:
        shorter (tuple[str, ...]): The candidate subsequence.
        longer (tuple[str, ...]): The sequence it is looked for in.

    Returns:
        bool: True iff every token of `shorter` appears in `longer`, in order.
    """
    remaining = iter(longer)
    return all(token in remaining for token in shorter)


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


def _check_clause_placement(snapshot: bytes, tokens: list[_Token]) -> list[Violation]:
    """Check the part of rule 2 that concerns contract clauses: none inside a function body.

    Args:
        snapshot (bytes): The session's copy.
        tokens (list[_Token]): Its tokens.

    Returns:
        list[Violation]: One violation per contract clause at a brace depth above zero.
    """
    _, spans = strip_cbmc_clauses(snapshot)
    offsets = [token.offset for token in tokens]
    violations = []
    for span in spans:
        preceding = bisect_right(offsets, span.start_byte) - 1
        if preceding < 0:
            continue
        token = tokens[preceding]
        depth = token.depth + (1 if token.text == "{" else 0) - (1 if token.text == "}" else 0)
        if depth > 0:
            violations.append(
                Violation(
                    2,
                    f"{span.kind} is inside a function body; a contract clause belongs between "
                    "the function's signature and its opening brace",
                    snapshot.count(b"\n", 0, span.start_byte) + 1,
                )
            )
    return violations


def _check_function_intact(
    base_items: list[_Item], snapshot_items: list[_Item], function: str
) -> list[Violation]:
    """Check rule 3: the session's function is still defined, as often as it was.

    A function may legitimately be defined more than once, in alternative preprocessor branches;
    what must not change is how many definitions there are.

    Args:
        base_items (list[_Item]): The base's items.
        snapshot_items (list[_Item]): The snapshot's items.
        function (str): The function the session works on.

    Returns:
        list[Violation]: Empty, or the one violation.
    """
    before = _count_definitions(base_items, function)
    after = _count_definitions(snapshot_items, function)
    if after == before or (before == 0 and after == 1):
        return []
    if after == 0:
        return [
            Violation(
                3,
                f"{function} is missing or is no longer one well-formed definition; a contract "
                "cannot be merged onto it",
            )
        ]
    return [
        Violation(
            3,
            f"{function} is defined {after} time(s) but was defined {before} time(s) when the "
            "session started; do not add or remove definitions of it",
        )
    ]


def _count_definitions(items: list[_Item], function: str) -> int:
    """Return how many function definitions named `function` are among `items`.

    Args:
        items (list[_Item]): A file's items.
        function (str): A function name.

    Returns:
        int: The number of definitions.
    """
    return sum(1 for item in items if item.kind == "function" and item.name == function)


def _check_new_items(
    base_items: list[_Item], snapshot_items: list[_Item], new_indices: list[int]
) -> list[Violation]:
    """Check rules 4 and 5 on the items the session added.

    Args:
        base_items (list[_Item]): The base's items.
        snapshot_items (list[_Item]): The snapshot's items.
        new_indices (list[int]): Indices of the snapshot items the session added.

    Returns:
        list[Violation]: The violations.
    """
    new = set(new_indices)
    existing_names = {item.name for item in base_items if item.name}
    violations: list[Violation] = []
    for index in new_indices:
        item = snapshot_items[index]
        violations += _check_new_item(item, existing_names)
        for text, line in zip(item.tokens, item.lines, strict=True):
            if text.startswith("#") and item.kind != "directive":
                violations += _check_new_directive(text, line, existing_names)
            if _ASSUME.search(text):
                violations.append(
                    Violation(
                        5,
                        "__CPROVER_assume is not allowed in a helper or macro; state what you "
                        "need as a precondition of the function instead",
                        line,
                    )
                )
    return violations + _check_conditional_nesting(snapshot_items, new)


def _check_new_item(item: _Item, existing_names: set[str]) -> list[Violation]:
    """Check that one added item introduces a reserved-prefix name and nothing else.

    Args:
        item (_Item): An item the session added.
        existing_names (set[str]): Names the base's items introduce.

    Returns:
        list[Violation]: The rule 4 violations of the item itself.
    """
    if item.kind == "directive":
        return _check_new_directive(item.tokens[0], item.line, existing_names)
    if item.kind == "incomplete":
        return [
            Violation(
                4,
                f"`{_quote(item.tokens)}` is not a complete declaration or definition",
                item.line,
            )
        ]
    if item.name is None:
        return [
            Violation(
                4,
                f"could not tell which name `{_quote(item.tokens)}` introduces; write it as a "
                f"plain declaration or definition of a name starting with {RESERVED_PREFIXES[1]}",
                item.line,
            )
        ]
    return _check_new_name(item.name, item.describe(), item.line, existing_names)


def _check_new_directive(text: str, line: int, existing_names: set[str]) -> list[Violation]:
    """Check one added preprocessor directive against rule 4.

    Args:
        text (str): The directive, normalized.
        line (int): Its line in the snapshot.
        existing_names (set[str]): Names the base's items introduce.

    Returns:
        list[Violation]: Empty for a conditional directive (checked for nesting separately) and
            for a `#define` of a new reserved-prefix name; otherwise the violation.
    """
    match = _DIRECTIVE_HEAD.match(text)
    keyword = match.group(1) if match else ""
    name = match.group(2) if match else ""
    if keyword in _CONDITIONAL_OPENERS | _CONDITIONAL_BRANCHES | {"endif"}:
        return []
    if keyword == "define":
        return _check_new_name(name, f"macro {name}", line, existing_names)
    reasons = {
        "undef": "it can change which branch of an existing #if is compiled",
        "pragma": "it can switch off the checks that make a verification mean something",
        "include": "it can bring in definitions that change existing code",
    }
    reason = reasons.get(keyword, "only #define and conditional directives may be added")
    return [Violation(4, f"new `#{keyword}` is not allowed: {reason}", line)]


def _check_new_name(
    name: str, description: str, line: int, existing_names: set[str]
) -> list[Violation]:
    """Check that an added name has a reserved prefix and is not already taken.

    Args:
        name (str): The name the added item introduces.
        description (str): The item, for the message.
        line (int): The item's line in the snapshot.
        existing_names (set[str]): Names the base's items introduce.

    Returns:
        list[Violation]: Empty, or the one rule 4 violation.
    """
    if not name.startswith(RESERVED_PREFIXES):
        return [
            Violation(
                4,
                f"{description} is new but its name does not start with {RESERVED_PREFIXES[0]} "
                f"(macros) or {RESERVED_PREFIXES[1]} (functions, variables, types); rename it, "
                "or remove it if it models a library function",
                line,
            )
        ]
    if name in existing_names:
        return [
            Violation(
                4,
                f"{description} redefines a name that already exists in the file; choose a "
                "new name",
                line,
            )
        ]
    return []


def _check_conditional_nesting(snapshot_items: list[_Item], new: set[int]) -> list[Violation]:
    """Check that an added `#if` block encloses only added items, and vice versa.

    Args:
        snapshot_items (list[_Item]): The snapshot's items.
        new (set[int]): Indices of the items the session added.

    Returns:
        list[Violation]: One violation per added conditional that encloses original code or that
            adds a branch to, or closes, an original conditional.
    """
    violations: list[Violation] = []
    open_blocks: list[tuple[bool, int]] = []  # (the opener is new, its line)
    reported: set[int] = set()
    for index, item in enumerate(snapshot_items):
        is_new = index in new
        keyword = item.directive
        if keyword in _CONDITIONAL_OPENERS:
            open_blocks.append((is_new, item.line))
        elif keyword in _CONDITIONAL_BRANCHES or keyword == "endif":
            opener_is_new = open_blocks[-1][0] if open_blocks else False
            if is_new and not opener_is_new:
                violations.append(
                    Violation(
                        4,
                        f"new `#{keyword}` changes an existing conditional block; a new "
                        "conditional may enclose only new, prefixed items",
                        item.line,
                    )
                )
            if keyword == "endif" and open_blocks:
                open_blocks.pop()
        elif not is_new:
            for opener_is_new, line in open_blocks:
                if opener_is_new and line not in reported:
                    reported.add(line)
                    violations.append(
                        Violation(
                            4,
                            "this new conditional block encloses original code "
                            f"({item.describe()}, line {item.line}); a new conditional may "
                            "enclose only new, prefixed items",
                            line,
                        )
                    )
    return violations


def _check_preconditions(base: bytes, snapshot: bytes) -> list[Violation]:
    """Check rule 8: no `__CPROVER_requires` clause the session wrote is constant false.

    Args:
        base (bytes): The file as it was when the session started.
        snapshot (bytes): The session's copy.

    Returns:
        list[Violation]: One violation per such clause that the base did not already contain.
    """
    already_there = {text for _, text in _false_preconditions(base)}
    return [
        Violation(
            8,
            f"__CPROVER_requires({text}) is false for every input, so the contract verifies "
            "while proving nothing; state the real precondition",
            line,
        )
        for line, text in _false_preconditions(snapshot)
        if text not in already_there
    ]


def _false_preconditions(source: bytes) -> list[tuple[int, str]]:
    """Return the constant-false `__CPROVER_requires` clauses of a file.

    Args:
        source (bytes): A C source.

    Returns:
        list[tuple[int, str]]: The line and normalized argument of each such clause.
    """
    _, spans = strip_cbmc_clauses(source)
    found = []
    for span in spans:
        if span.kind != "__CPROVER_requires":
            continue
        clause = source[span.start_byte : span.end_byte].decode("latin-1")
        argument = "".join(_COMMENT.sub(" ", clause[clause.index("(") :]).split())
        while argument.startswith("(") and argument.endswith(")") and _wraps_whole(argument):
            argument = argument[1:-1]
        if argument in _FALSE_PRECONDITIONS:
            found.append((source.count(b"\n", 0, span.start_byte) + 1, argument))
    return found


def _wraps_whole(text: str) -> bool:
    """Return True iff the first `(` of `text` is closed by its last character.

    Args:
        text (str): A string starting with `(` and ending with `)`.

    Returns:
        bool: True iff those two parentheses are a pair.
    """
    depth = 0
    for index, character in enumerate(text):
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth == 0:
                return index == len(text) - 1
    return False
