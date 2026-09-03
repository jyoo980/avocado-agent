"""Persistent, edit-stable memory of per-mutant verification verdicts.

Mutation testing re-runs the full CBMC pipeline once per mutant, and today it does so from
scratch on every `avocado-run-cbmc` invocation. An agent that runs the tool several times while
iterating on one specification therefore pays repeatedly for mutants whose verdict cannot have
changed — including mutants that do not compile at all, and mutants that no specification has
ever killed. This module remembers those verdicts so `tools.util.mutation` can skip them.

The hard part is naming a mutant in a way that survives the very edit the cache exists to
tolerate. `Mutant` carries only positional coordinates (`start_byte`, `end_byte`, `line`,
`column`), and every one of them shifts the moment the agent inserts a `__CPROVER_ensures` clause
above the function. The way out is that CBMC contract clauses sit *between* a function's
declarator and its body (see `tools.util.cbmc_clause_stripper`), and `CLAUDE.md` forbids the agent
from editing the C code itself — so a function's body is invariant across specification edits, and
an offset measured *relative to the start of the body* is stable. That relative offset, plus the
operator being swapped, is the mutant's identity.

Two safety nets keep the scheme honest when its assumptions break:

- Each function's entry records a `body_digest`. If the body ever does change, the entry is
  discarded wholesale rather than trusted, so a violated assumption costs time, not correctness.
- Verdicts are recorded against a `spec_digest` that covers the function's own contract *and* the
  contracts of its transitively-reachable in-file callees, because the pipeline substitutes callee
  contracts via `--replace-call-with-contract`; a callee's specification genuinely can change this
  function's mutant verdicts.

Reusing a verdict recorded under the *same* `spec_digest` is exact. The remaining skip rules
(`PRESUMED_EQUIVALENT`, `CHRONIC_TIMEOUT`) are explicitly heuristic — a mutant that no
specification has killed so far may still be killable — which is why they are reported in their
own bucket rather than folded into the kill score, and why `--recheck-equivalent` exists to
re-test them.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from tools.util.tree_sitter_utils import get_cbmc_contract_text, get_function_body

if TYPE_CHECKING:
    from eval.mutants.mutate_function import Mutant
    from tools.util.callgraph import CallGraph

# Suffix of the sibling JSON file holding the cache, mirroring the `<stem>-callgraph.json`
# convention already used by `tools.construct_call_graph`. Public so `make clean` and tests can
# name it.
MUTATION_CACHE_SUFFIX = "-mutation-cache.json"

# Bumped whenever the on-disk schema changes incompatibly; a mismatch is treated as an empty
# cache rather than an error, so an old file costs a recompute and nothing worse.
_CACHE_VERSION = 1

# How many *distinct* specifications a mutant must survive before it is presumed equivalent.
# Deliberately conservative: a mutant that three materially different specs all failed to kill is
# far more likely to be semantically equivalent to the original than to be waiting on a fourth.
_PRESUMED_EQUIVALENT_DISTINCT_SPECS = 3

# How many distinct specifications a mutant must time out under before CBMC is presumed unable to
# decide it. Timed-out mutants are already excluded from the kill score, so skipping them costs no
# signal -- it only stops paying the timeout again.
_CHRONIC_TIMEOUT_DISTINCT_SPECS = 2

# Length of the hex digest used for mutant ids. 16 hex chars (64 bits) makes a collision within a
# single function's mutant set vanishingly unlikely while staying short enough to paste into an
# `avocado-mark-equivalent` invocation.
_MUTANT_ID_CHARS = 16


class MutantOutcome(StrEnum):
    """The decided outcome of verifying one mutant.

    Mirrors the buckets `tools.util.mutation` aggregates over, so a cached outcome can be replayed
    into a `MutantVerificationResult` without translation.
    """

    KILLED = "killed"
    SURVIVED = "survived"
    TIMED_OUT = "timed_out"
    COMPILE_FAILED = "compile_failed"
    INSTRUMENTATION_FAILED = "instrumentation_failed"


class SkipReason(StrEnum):
    """Why a mutant was not re-verified on this run.

    `MEMOIZED` and `PERMANENT` are exact: the verdict could not have changed. The rest are
    heuristics, and each is surfaced to the client so a skipped mutant is never silently invisible.
    """

    MEMOIZED = "memoized"
    PERMANENT = "permanent"
    PRESUMED_EQUIVALENT = "presumed_equivalent"
    AGENT_DECLARED = "agent_declared"
    CHRONIC_TIMEOUT = "chronic_timeout"


# Outcomes that are a property of the mutated *body* rather than of the specification, and so can
# be replayed forever (subject to the `body_digest` check). Mutation testing only runs after the
# unmutated function verified, which means goto-cc and goto-instrument both succeeded on the
# original source under the current spec; a failure that appears only for a mutant is therefore
# attributable to the mutation itself.
_PERMANENT_OUTCOMES = frozenset(
    {MutantOutcome.COMPILE_FAILED, MutantOutcome.INSTRUMENTATION_FAILED}
)

# Skip reasons that mean "this mutant is believed unkillable", as opposed to "we already know the
# answer". These populate the `presumed_equivalent` bucket.
_PRESUMED_EQUIVALENT_REASONS = frozenset(
    {SkipReason.AGENT_DECLARED, SkipReason.PRESUMED_EQUIVALENT}
)


@dataclass(frozen=True)
class SkipDecision:
    """A decision to reuse a remembered verdict instead of re-running CBMC.

    Attributes:
        reason (SkipReason): Why the mutant was skipped.
        outcome (MutantOutcome): The verdict to replay for this mutant.
        detail (str): Human-readable justification, shown to the client for declared-equivalent
            mutants and used in debug logging otherwise.
    """

    reason: SkipReason
    outcome: MutantOutcome
    detail: str = ""

    @property
    def is_presumed_equivalent(self) -> bool:
        """True iff the mutant is *believed* unkillable, rather than its verdict being known."""
        return self.reason in _PRESUMED_EQUIVALENT_REASONS


def cache_path_for(source_path: str | Path) -> Path:
    """Return the path of the mutation cache that sits beside `source_path`.

    Args:
        source_path (str | Path): Path to the C source file under verification.

    Returns:
        Path: The sibling `<stem>-mutation-cache.json` path.
    """
    resolved = Path(source_path).resolve()
    return resolved.with_name(f"{resolved.stem}{MUTATION_CACHE_SUFFIX}")


def compute_mutant_id(mutant: Mutant, body_start_byte: int) -> str:
    """Return a stable identifier for `mutant`, independent of the surrounding specification.

    The identity is the operator being swapped plus its offset *from the start of the enclosing
    function's body*. Because a mutation replaces an operator token in place, every byte before the
    mutation point is identical in the original and the mutant, so `body_start_byte` — taken from
    the original source — is the correct origin for the mutant's `start_byte`. Inserting or
    rewriting contract clauses shifts both values equally and leaves the difference untouched.

    Args:
        mutant (Mutant): The mutant to identify.
        body_start_byte (int): Byte offset of the enclosing function's body in the source.

    Returns:
        str: A 16-character hex identifier.
    """
    key = "|".join(
        (
            mutant.function,
            str(mutant.operator_class),
            mutant.original_operator,
            mutant.replacement_operator,
            str(mutant.start_byte - body_start_byte),
        )
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:_MUTANT_ID_CHARS]


def compute_body_digest(source_path: str, function: str) -> str:
    """Return a digest of `function`'s body text, used to detect edits to the C code itself.

    In-body CBMC intrinsics (`__CPROVER_assume`, `__CPROVER_assert`) are *not* excluded: the mutant
    generator skips operators inside them, so adding one genuinely changes the mutant population
    and must invalidate the cache.

    Args:
        source_path (str): Path to the C file defining the function.
        function (str): The function whose body to digest.

    Returns:
        str: A hex digest, or "" when the function's body cannot be located.
    """
    body = get_function_body(source_path, function)
    if body is None or body.text is None:
        return ""
    return hashlib.sha256(body.text).hexdigest()


def compute_spec_digest(
    source_path: str, function: str, call_graph: CallGraph | None = None
) -> str:
    """Return a digest of the specification `function` is verified against.

    Covers the function's own contract plus the contracts of every in-file function reachable from
    it, because `goto-instrument --replace-call-with-contract` substitutes callee contracts at
    their call sites: strengthening a callee's spec can change which of *this* function's mutants
    CBMC kills. Callee names are sorted so the digest does not depend on traversal order, and the
    walk is cycle-guarded for (mutually) recursive functions.

    Args:
        source_path (str): Path to the C file defining the function.
        function (str): The function under verification.
        call_graph (CallGraph | None): Call graph of the file. When None, only the function's own
            contract is covered.

    Returns:
        str: A hex digest of the relevant contract text.
    """
    contracts = [
        f"{name}::{get_cbmc_contract_text(source_path, name)}"
        for name in _reachable_in_file_functions(function, call_graph)
    ]
    return hashlib.sha256("\n".join(contracts).encode("utf-8")).hexdigest()


def _reachable_in_file_functions(function: str, call_graph: CallGraph | None) -> list[str]:
    """Return `function` plus its transitively-reachable in-file callees, sorted.

    Args:
        function (str): The root function.
        call_graph (CallGraph | None): Call graph of the file, or None for no callees.

    Returns:
        list[str]: Sorted, de-duplicated function names including `function` itself.
    """
    if call_graph is None:
        return [function]
    reachable: set[str] = set()
    stack = [function]
    while stack:
        current = stack.pop()
        if current in reachable:
            continue
        reachable.add(current)
        try:
            stack.extend(call_graph.get_callees(current).internal)
        except (KeyError, AttributeError):
            # A callee absent from the graph contributes no contract; it is not an error.
            continue
    return sorted(reachable)


def decide_skip(entry: dict, spec_digest: str) -> SkipDecision | None:
    """Return how to skip the mutant described by `entry`, or None to verify it normally.

    Rules are ordered by how much authority the evidence carries:

    1. A permanent, body-attributable failure — the specification cannot affect it.
    2. Proof that this very specification *kills* the mutant. This is the only evidence that
       contradicts a claim of equivalence, so it outranks one.
    3. An explicit agent declaration of equivalence.
    4. Survival across enough distinct specifications to infer equivalence.
    5. Any other verdict recorded under this very specification.
    6. Repeated timeouts that suggest CBMC simply cannot decide this mutant.

    Steps 3 and 4 deliberately sit *above* the general memoization in step 5: a remembered
    "survived" verdict is consistent with equivalence rather than evidence against it, so it must
    not quietly demote a presumed-equivalent mutant back to an ordinary survivor. Getting this
    order wrong would make an equivalence judgement flicker on and off depending on whether the
    current specification happened to appear in the mutant's history.

    Args:
        entry (dict): The cached per-mutant record.
        spec_digest (str): Digest of the specification about to be verified against.

    Returns:
        SkipDecision | None: The decision to reuse a verdict, or None if the mutant must be run.
    """
    permanent = entry.get("permanent")
    if isinstance(permanent, dict) and (outcome := _as_outcome(permanent.get("outcome"))):
        return SkipDecision(SkipReason.PERMANENT, outcome, "outcome depends only on the body")

    history = [record for record in entry.get("history", []) if isinstance(record, dict)]
    memoized = _memoized_outcome(history, spec_digest)
    if memoized is MutantOutcome.KILLED:
        return SkipDecision(SkipReason.MEMOIZED, memoized, "specification unchanged")

    declared = entry.get("declared_equivalent")
    if isinstance(declared, dict):
        reason = str(declared.get("reason", "")).strip()
        return SkipDecision(SkipReason.AGENT_DECLARED, MutantOutcome.SURVIVED, reason)

    survived_under = _distinct_specs_with(history, MutantOutcome.SURVIVED)
    if len(survived_under) >= _PRESUMED_EQUIVALENT_DISTINCT_SPECS:
        return SkipDecision(
            SkipReason.PRESUMED_EQUIVALENT,
            MutantOutcome.SURVIVED,
            f"survived {len(survived_under)} distinct specifications",
        )

    if memoized is not None:
        return SkipDecision(SkipReason.MEMOIZED, memoized, "specification unchanged")

    timed_out_under = _distinct_specs_with(history, MutantOutcome.TIMED_OUT)
    if len(timed_out_under) >= _CHRONIC_TIMEOUT_DISTINCT_SPECS:
        return SkipDecision(
            SkipReason.CHRONIC_TIMEOUT,
            MutantOutcome.TIMED_OUT,
            f"timed out under {len(timed_out_under)} distinct specifications",
        )
    return None


def _memoized_outcome(history: list[dict], spec_digest: str) -> MutantOutcome | None:
    """Return the most recent outcome recorded under exactly `spec_digest`, if any.

    Args:
        history (list[dict]): The mutant's recorded verdicts, oldest first.
        spec_digest (str): The specification digest to match.

    Returns:
        MutantOutcome | None: The remembered outcome, or None when this specification is new.
    """
    for record in reversed(history):
        if record.get("spec_digest") == spec_digest and (
            outcome := _as_outcome(record.get("outcome"))
        ):
            return outcome
    return None


def _distinct_specs_with(history: list[dict], outcome: MutantOutcome) -> set[str]:
    """Return the distinct `spec_digest`s under which `outcome` was recorded.

    Args:
        history (list[dict]): The mutant's recorded verdicts.
        outcome (MutantOutcome): The outcome to count.

    Returns:
        set[str]: Distinct specification digests carrying that outcome.
    """
    return {
        str(record["spec_digest"])
        for record in history
        if record.get("outcome") == str(outcome) and record.get("spec_digest")
    }


def _as_outcome(value: object) -> MutantOutcome | None:
    """Return `value` as a `MutantOutcome`, or None when it is absent or unrecognized.

    Args:
        value (object): A raw value read from the cache file.

    Returns:
        MutantOutcome | None: The parsed outcome, or None.
    """
    try:
        return MutantOutcome(str(value))
    except ValueError:
        return None


class MutationCache:
    """Read/modify/write access to the mutation cache beside one C source file.

    Every filesystem interaction is best-effort: a missing, unreadable, or corrupt cache behaves
    exactly like an empty one, and a failed write is logged and swallowed. Losing the cache costs
    time, never correctness, so it must never be able to break a verification run.
    """

    def __init__(self, path: Path, data: dict) -> None:
        """Create a cache backed by `path`.

        Prefer `MutationCache.load`; this constructor exists for tests that supply data directly.

        Args:
            path (Path): The cache file's path.
            data (dict): The cache contents.
        """
        self._path = path
        self._data = data

    @classmethod
    def load(cls, source_path: str | Path) -> MutationCache:
        """Return the cache beside `source_path`, or an empty one when it cannot be read.

        Args:
            source_path (str | Path): Path to the C source file under verification.

        Returns:
            MutationCache: The loaded cache; empty on any read or schema problem.
        """
        path = cache_path_for(source_path)
        empty = {"version": _CACHE_VERSION, "functions": {}}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls(path, empty)
        if not isinstance(data, dict) or data.get("version") != _CACHE_VERSION:
            return cls(path, empty)
        if not isinstance(data.get("functions"), dict):
            return cls(path, empty)
        return cls(path, data)

    @property
    def path(self) -> Path:
        """The cache file's path."""
        return self._path

    def entries_for(self, function: str, body_digest: str) -> dict[str, dict]:
        """Return the per-mutant records for `function`, discarding them if its body changed.

        Args:
            function (str): The function whose records to return.
            body_digest (str): Digest of the function's current body.

        Returns:
            dict[str, dict]: Mutant id to cached record. Empty when nothing usable is stored.
        """
        entry = self._data["functions"].get(function)
        if not isinstance(entry, dict):
            return {}
        if entry.get("body_digest") != body_digest:
            # The C code itself changed, so mutant identities and verdicts alike are meaningless.
            if entry.get("body_digest"):
                logger.info(f"{function}: body changed; discarding cached mutant verdicts")
            self._data["functions"].pop(function, None)
            return {}
        mutants = entry.get("mutants")
        return mutants if isinstance(mutants, dict) else {}

    def record(
        self,
        function: str,
        body_digest: str,
        mutant_id: str,
        *,
        mutant: Mutant,
        body_offset: int,
        spec_digest: str,
        outcome: MutantOutcome,
    ) -> None:
        """Remember one mutant's verdict.

        Outcomes in `_PERMANENT_OUTCOMES` are additionally pinned as `permanent`, since they follow
        from the mutated body alone and cannot be undone by a stronger specification.

        Args:
            function (str): The function the mutant belongs to.
            body_digest (str): Digest of the function's current body.
            mutant_id (str): The mutant's stable identifier.
            mutant (Mutant): The mutant, for the human-readable descriptive fields.
            body_offset (int): The mutant's offset from the start of the function body.
            spec_digest (str): Digest of the specification it was verified against.
            outcome (MutantOutcome): The verdict to record.
        """
        functions = self._data.setdefault("functions", {})
        entry = functions.setdefault(function, {"body_digest": body_digest, "mutants": {}})
        entry["body_digest"] = body_digest
        mutants = entry.setdefault("mutants", {})
        record = mutants.setdefault(
            mutant_id,
            {
                "operator_class": str(mutant.operator_class),
                "original_operator": mutant.original_operator,
                "replacement_operator": mutant.replacement_operator,
                "body_offset": body_offset,
                "history": [],
            },
        )
        record.setdefault("history", []).append(
            {
                "spec_digest": spec_digest,
                "outcome": str(outcome),
                "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        )
        if outcome in _PERMANENT_OUTCOMES:
            record["permanent"] = {"outcome": str(outcome)}

    def declare_equivalent(
        self, function: str, mutant_id: str, *, reason: str, declared_by: str = "agent"
    ) -> bool:
        """Mark a mutant as equivalent so future runs skip it.

        Args:
            function (str): The function the mutant belongs to.
            mutant_id (str): The mutant's stable identifier.
            reason (str): Justification, recorded so the declaration stays auditable.
            declared_by (str): Who declared it.

        Returns:
            bool: True iff a mutant with that id is known for `function`.
        """
        entry = self._data.get("functions", {}).get(function)
        if not isinstance(entry, dict):
            return False
        record = entry.get("mutants", {}).get(mutant_id)
        if not isinstance(record, dict):
            return False
        record["declared_equivalent"] = {
            "by": declared_by,
            "reason": reason,
            "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        return True

    def known_mutant_ids(self, function: str) -> list[str]:
        """Return the mutant ids recorded for `function`, in insertion order.

        Args:
            function (str): The function whose mutant ids to list.

        Returns:
            list[str]: The recorded mutant ids; empty when the function is unknown.
        """
        entry = self._data.get("functions", {}).get(function)
        if not isinstance(entry, dict):
            return []
        mutants = entry.get("mutants")
        return list(mutants) if isinstance(mutants, dict) else []

    def save(self) -> None:
        """Write the cache to disk, swallowing any I/O error.

        The write goes to a temporary sibling and is then atomically renamed, so a run interrupted
        mid-write leaves the previous cache intact rather than a truncated file that the next run
        would have to discard.
        """
        temporary = self._path.with_suffix(f"{self._path.suffix}.tmp")
        try:
            temporary.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
            temporary.replace(self._path)
        except OSError as error:
            logger.warning(f"could not write mutation cache {self._path}: {error}")
            temporary.unlink(missing_ok=True)
