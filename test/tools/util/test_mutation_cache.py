"""Tests for the persistent mutation cache and its skip policy."""

from __future__ import annotations

import json
from pathlib import Path

from eval.mutants.mutate_function import Mutant

from tools.util.callgraph import CallGraph
from tools.util.mutation_cache import (
    MutantOutcome,
    MutationCache,
    SkipReason,
    cache_path_for,
    compute_body_digest,
    compute_mutant_id,
    compute_spec_digest,
    decide_skip,
)
from tools.util.tree_sitter_utils import get_function_body

_UNSPECIFIED = """\
int add(int a, int b) {
  return a + b;
}
"""

# The same code, with a contract inserted between the declarator and the body. Every byte offset
# and line number inside the body shifts; the body itself is untouched.
_SPECIFIED = """\
int add(int a, int b)
  __CPROVER_requires(a >= 0 && b >= 0)
  __CPROVER_ensures(__CPROVER_return_value == a + b)
{
  return a + b;
}
"""

_STRONGER_SPEC = """\
int add(int a, int b)
  __CPROVER_requires(a >= 0 && b >= 100)
  __CPROVER_ensures(__CPROVER_return_value == a + b)
{
  return a + b;
}
"""


def _mutant(*, function: str = "add", start_byte: int, source: str) -> Mutant:
    """Build a Mutant standing in for an `a + b` -> `a - b` swap at `start_byte`."""
    return Mutant(
        function=function,
        operator_class="ARITHMETIC",
        original_operator="+",
        replacement_operator="-",
        start_byte=start_byte,
        end_byte=start_byte + 1,
        line=1,
        column=0,
        mutant_source=source,
    )


def _write(tmp_path: Path, name: str, source: str) -> str:
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    return str(path)


def _body_start(source_path: str, function: str) -> int:
    body = get_function_body(source_path, function)
    assert body is not None
    return body.start_byte


def test_mutant_id_is_stable_when_a_contract_is_inserted(tmp_path: Path) -> None:
    """Adding a contract shifts every offset, but must not change a mutant's identity."""
    unspecified = _write(tmp_path, "a.c", _UNSPECIFIED)
    specified = _write(tmp_path, "b.c", _SPECIFIED)

    unspecified_body = _body_start(unspecified, "add")
    specified_body = _body_start(specified, "add")
    # The contract really did move the body, otherwise this test proves nothing.
    assert specified_body > unspecified_body

    offset_into_body = _UNSPECIFIED.index("a + b") + 2 - unspecified_body
    before = _mutant(start_byte=unspecified_body + offset_into_body, source=_UNSPECIFIED)
    after = _mutant(start_byte=specified_body + offset_into_body, source=_SPECIFIED)

    assert compute_mutant_id(before, unspecified_body) == compute_mutant_id(after, specified_body)


def test_mutant_id_distinguishes_operator_and_position() -> None:
    """Different swaps, and the same swap at different body offsets, are different mutants."""
    base = _mutant(start_byte=50, source=_UNSPECIFIED)
    elsewhere = _mutant(start_byte=60, source=_UNSPECIFIED)
    other_replacement = Mutant(
        function="add",
        operator_class="ARITHMETIC",
        original_operator="+",
        replacement_operator="*",
        start_byte=50,
        end_byte=51,
        line=1,
        column=0,
        mutant_source=_UNSPECIFIED,
    )

    identifiers = {
        compute_mutant_id(base, 10),
        compute_mutant_id(elsewhere, 10),
        compute_mutant_id(other_replacement, 10),
    }
    assert len(identifiers) == 3


def test_body_digest_ignores_the_contract_but_tracks_the_code(tmp_path: Path) -> None:
    """A contract edit leaves the body digest alone; a code edit changes it."""
    unspecified = _write(tmp_path, "a.c", _UNSPECIFIED)
    specified = _write(tmp_path, "b.c", _SPECIFIED)
    edited = _write(tmp_path, "c.c", _UNSPECIFIED.replace("a + b", "a * b"))

    assert compute_body_digest(unspecified, "add") == compute_body_digest(specified, "add")
    assert compute_body_digest(unspecified, "add") != compute_body_digest(edited, "add")


def test_body_digest_is_empty_for_an_unknown_function(tmp_path: Path) -> None:
    """A function that is not in the file yields no digest rather than raising."""
    source = _write(tmp_path, "a.c", _UNSPECIFIED)
    assert compute_body_digest(source, "nonexistent") == ""


def test_spec_digest_tracks_the_contract(tmp_path: Path) -> None:
    """The specification digest changes exactly when the contract's meaning changes."""
    unspecified = _write(tmp_path, "a.c", _UNSPECIFIED)
    specified = _write(tmp_path, "b.c", _SPECIFIED)
    stronger = _write(tmp_path, "c.c", _STRONGER_SPEC)
    reindented = _write(tmp_path, "d.c", _SPECIFIED.replace("  __CPROVER", "\t\t__CPROVER"))

    assert compute_spec_digest(unspecified, "add") != compute_spec_digest(specified, "add")
    assert compute_spec_digest(specified, "add") != compute_spec_digest(stronger, "add")
    # Whitespace is normalized, so re-indenting a clause does not invalidate the cache.
    assert compute_spec_digest(specified, "add") == compute_spec_digest(reindented, "add")


def test_spec_digest_covers_callee_contracts(tmp_path: Path) -> None:
    """A callee's contract is substituted at the call site, so it must key the cache too."""
    caller_with_weak_callee = """\
int helper(int x)
  __CPROVER_ensures(__CPROVER_return_value >= 0)
{
  return x;
}

int caller(int y) {
  return helper(y) + 1;
}
"""
    caller_with_strong_callee = caller_with_weak_callee.replace(
        "__CPROVER_return_value >= 0", "__CPROVER_return_value == x"
    )
    weak = _write(tmp_path, "weak.c", caller_with_weak_callee)
    strong = _write(tmp_path, "strong.c", caller_with_strong_callee)
    call_graph = CallGraph({"helper": {"internal": [], "external": []},
                            "caller": {"internal": ["helper"], "external": []}})

    # `caller`'s own contract is identical in both; only `helper`'s changed.
    assert compute_spec_digest(weak, "caller", call_graph) != compute_spec_digest(
        strong, "caller", call_graph
    )
    # Without a call graph, only the function's own contract is covered, so they agree.
    assert compute_spec_digest(weak, "caller", None) == compute_spec_digest(
        strong, "caller", None
    )


def test_spec_digest_handles_recursive_call_graphs(tmp_path: Path) -> None:
    """A self- or mutually-recursive call graph must not send the walk into a loop."""
    source = _write(tmp_path, "a.c", _UNSPECIFIED)
    call_graph = CallGraph({"add": {"internal": ["add"], "external": []}})
    assert compute_spec_digest(source, "add", call_graph)


def test_decide_skip_returns_none_for_an_unseen_mutant() -> None:
    """A mutant with no history must actually be verified."""
    assert decide_skip({}, "spec-1") is None


def test_decide_skip_memoizes_an_unchanged_specification() -> None:
    """The same specification cannot produce a different verdict, so the verdict is reused."""
    entry = {"history": [{"spec_digest": "spec-1", "outcome": "killed"}]}

    decision = decide_skip(entry, "spec-1")

    assert decision is not None
    assert decision.reason is SkipReason.MEMOIZED
    assert decision.outcome is MutantOutcome.KILLED
    assert not decision.is_presumed_equivalent
    # A different specification is not memoized.
    assert decide_skip(entry, "spec-2") is None


def test_decide_skip_replays_a_permanent_compile_failure() -> None:
    """A mutant goto-cc rejects will be rejected again, whatever the specification says."""
    entry = {
        "permanent": {"outcome": "compile_failed"},
        "history": [{"spec_digest": "spec-1", "outcome": "compile_failed"}],
    }

    decision = decide_skip(entry, "spec-99")

    assert decision is not None
    assert decision.reason is SkipReason.PERMANENT
    assert decision.outcome is MutantOutcome.COMPILE_FAILED
    assert not decision.is_presumed_equivalent


def test_decide_skip_presumes_equivalence_after_three_distinct_specifications() -> None:
    """Surviving three genuinely different specifications is the equivalence heuristic."""
    two_specs = {
        "history": [
            {"spec_digest": "spec-1", "outcome": "survived"},
            {"spec_digest": "spec-2", "outcome": "survived"},
        ]
    }
    assert decide_skip(two_specs, "spec-3") is None

    three_specs = {
        "history": [
            *two_specs["history"],
            {"spec_digest": "spec-3", "outcome": "survived"},
        ]
    }
    decision = decide_skip(three_specs, "spec-4")

    assert decision is not None
    assert decision.reason is SkipReason.PRESUMED_EQUIVALENT
    assert decision.is_presumed_equivalent


def test_decide_skip_does_not_count_one_specification_repeatedly() -> None:
    """Re-running the same specification must not accumulate toward the equivalence threshold."""
    entry = {
        "history": [
            {"spec_digest": "spec-1", "outcome": "survived"},
            {"spec_digest": "spec-1", "outcome": "survived"},
            {"spec_digest": "spec-1", "outcome": "survived"},
        ]
    }
    # The same spec is memoized, not presumed equivalent...
    assert decide_skip(entry, "spec-1").reason is SkipReason.MEMOIZED
    # ...and a new specification still gets a real run.
    assert decide_skip(entry, "spec-2") is None


def test_decide_skip_honors_an_agent_declaration() -> None:
    """An explicit declaration skips the mutant and reports the recorded reason."""
    entry = {"declared_equivalent": {"by": "agent", "reason": "swap is a no-op for all inputs"}}

    decision = decide_skip(entry, "spec-1")

    assert decision is not None
    assert decision.reason is SkipReason.AGENT_DECLARED
    assert decision.is_presumed_equivalent
    assert decision.detail == "swap is a no-op for all inputs"


def test_decide_skip_prefers_exact_evidence_over_a_declaration() -> None:
    """If this very specification killed the mutant, that beats a claim of equivalence."""
    entry = {
        "declared_equivalent": {"by": "agent", "reason": "believed equivalent"},
        "history": [{"spec_digest": "spec-1", "outcome": "killed"}],
    }

    decision = decide_skip(entry, "spec-1")

    assert decision.reason is SkipReason.MEMOIZED
    assert decision.outcome is MutantOutcome.KILLED
    assert not decision.is_presumed_equivalent


def test_a_declaration_outlives_a_remembered_survival() -> None:
    """A remembered 'survived' agrees with equivalence, so it must not demote the declaration.

    Regression: an audit run (`--recheck-equivalent`) records a fresh `survived` verdict for the
    current specification. If plain memoization outranked the declaration, that audit would
    silently un-declare the mutant and drop it back into the survivor bucket.
    """
    entry = {
        "declared_equivalent": {"by": "agent", "reason": "no-op swap"},
        "history": [{"spec_digest": "spec-1", "outcome": "survived"}],
    }

    decision = decide_skip(entry, "spec-1")

    assert decision.reason is SkipReason.AGENT_DECLARED
    assert decision.is_presumed_equivalent


def test_presumed_equivalence_outlives_a_remembered_survival() -> None:
    """The same stickiness applies to the inferred judgement, not just explicit declarations."""
    entry = {
        "history": [
            {"spec_digest": "spec-1", "outcome": "survived"},
            {"spec_digest": "spec-2", "outcome": "survived"},
            {"spec_digest": "spec-3", "outcome": "survived"},
        ]
    }

    # `spec-3` is in the history, so naive memoization would report a plain survivor.
    decision = decide_skip(entry, "spec-3")

    assert decision.reason is SkipReason.PRESUMED_EQUIVALENT
    assert decision.is_presumed_equivalent


def test_a_kill_under_the_current_specification_overrides_equivalence() -> None:
    """Proof the mutant is killable is the one thing that must beat an equivalence claim."""
    entry = {
        "declared_equivalent": {"by": "agent", "reason": "believed equivalent"},
        "history": [
            {"spec_digest": "spec-1", "outcome": "survived"},
            {"spec_digest": "spec-2", "outcome": "survived"},
            {"spec_digest": "spec-3", "outcome": "survived"},
            {"spec_digest": "spec-4", "outcome": "killed"},
        ],
    }

    decision = decide_skip(entry, "spec-4")

    assert decision.reason is SkipReason.MEMOIZED
    assert decision.outcome is MutantOutcome.KILLED
    assert not decision.is_presumed_equivalent


def test_decide_skip_gives_up_on_chronically_timing_out_mutants() -> None:
    """A mutant CBMC cannot decide under two specifications stops costing the timeout."""
    entry = {
        "history": [
            {"spec_digest": "spec-1", "outcome": "timed_out"},
            {"spec_digest": "spec-2", "outcome": "timed_out"},
        ]
    }

    decision = decide_skip(entry, "spec-3")

    assert decision is not None
    assert decision.reason is SkipReason.CHRONIC_TIMEOUT
    assert decision.outcome is MutantOutcome.TIMED_OUT
    # Undecided, but not a claim of equivalence.
    assert not decision.is_presumed_equivalent


def test_decide_skip_ignores_malformed_records() -> None:
    """Junk in the cache degrades to 'verify it' rather than raising."""
    entry = {"permanent": "not-a-dict", "history": ["nonsense", {"outcome": "bogus"}]}
    assert decide_skip(entry, "spec-1") is None


def test_cache_round_trips_a_verdict(tmp_path: Path) -> None:
    """A recorded verdict is readable by the next run."""
    source = _write(tmp_path, "a.c", _UNSPECIFIED)
    mutant = _mutant(start_byte=30, source=_UNSPECIFIED)
    digest = compute_body_digest(source, "add")
    mutant_id = compute_mutant_id(mutant, 20)

    cache = MutationCache.load(source)
    cache.record(
        "add",
        digest,
        mutant_id,
        mutant=mutant,
        body_offset=10,
        spec_digest="spec-1",
        outcome=MutantOutcome.SURVIVED,
    )
    cache.save()

    reloaded = MutationCache.load(source)
    entries = reloaded.entries_for("add", digest)
    assert mutant_id in entries
    assert decide_skip(entries[mutant_id], "spec-1").outcome is MutantOutcome.SURVIVED


def test_cache_is_discarded_when_the_body_changes(tmp_path: Path) -> None:
    """Mutant identities are body-relative, so a body edit invalidates everything."""
    source = _write(tmp_path, "a.c", _UNSPECIFIED)
    mutant = _mutant(start_byte=30, source=_UNSPECIFIED)

    cache = MutationCache.load(source)
    cache.record(
        "add",
        "body-digest-v1",
        compute_mutant_id(mutant, 20),
        mutant=mutant,
        body_offset=10,
        spec_digest="spec-1",
        outcome=MutantOutcome.SURVIVED,
    )
    cache.save()

    reloaded = MutationCache.load(source)
    assert reloaded.entries_for("add", "body-digest-v2") == {}


def test_cache_records_a_permanent_marker_for_compile_failures(tmp_path: Path) -> None:
    """A compile failure is pinned so it is never re-run, even under a new specification."""
    source = _write(tmp_path, "a.c", _UNSPECIFIED)
    mutant = _mutant(start_byte=30, source=_UNSPECIFIED)
    mutant_id = compute_mutant_id(mutant, 20)

    cache = MutationCache.load(source)
    cache.record(
        "add",
        "digest",
        mutant_id,
        mutant=mutant,
        body_offset=10,
        spec_digest="spec-1",
        outcome=MutantOutcome.COMPILE_FAILED,
    )

    entry = cache.entries_for("add", "digest")[mutant_id]
    assert decide_skip(entry, "a-totally-new-spec").reason is SkipReason.PERMANENT


def test_declare_equivalent_requires_a_known_mutant(tmp_path: Path) -> None:
    """Declaring an unknown mutant fails rather than inventing a cache entry."""
    source = _write(tmp_path, "a.c", _UNSPECIFIED)
    cache = MutationCache.load(source)
    assert not cache.declare_equivalent("add", "deadbeef", reason="nope")


def test_declare_equivalent_marks_a_known_mutant(tmp_path: Path) -> None:
    """A declaration is recorded with its justification and then skips the mutant."""
    source = _write(tmp_path, "a.c", _UNSPECIFIED)
    mutant = _mutant(start_byte=30, source=_UNSPECIFIED)
    mutant_id = compute_mutant_id(mutant, 20)
    cache = MutationCache.load(source)
    cache.record(
        "add",
        "digest",
        mutant_id,
        mutant=mutant,
        body_offset=10,
        spec_digest="spec-1",
        outcome=MutantOutcome.SURVIVED,
    )

    assert cache.declare_equivalent("add", mutant_id, reason="no-op swap")

    entry = cache.entries_for("add", "digest")[mutant_id]
    assert entry["declared_equivalent"]["reason"] == "no-op swap"
    assert decide_skip(entry, "brand-new-spec").reason is SkipReason.AGENT_DECLARED


def test_load_tolerates_a_corrupt_cache(tmp_path: Path) -> None:
    """A truncated or hand-mangled cache behaves as if it were absent."""
    source = _write(tmp_path, "a.c", _UNSPECIFIED)
    cache_path_for(source).write_text("{not json", encoding="utf-8")

    cache = MutationCache.load(source)

    assert cache.entries_for("add", "digest") == {}


def test_load_tolerates_a_future_schema_version(tmp_path: Path) -> None:
    """An unrecognized schema version is discarded rather than misread."""
    source = _write(tmp_path, "a.c", _UNSPECIFIED)
    cache_path_for(source).write_text(
        json.dumps({"version": 999, "functions": {"add": {"body_digest": "d", "mutants": {}}}}),
        encoding="utf-8",
    )

    assert MutationCache.load(source).entries_for("add", "d") == {}


def test_save_is_atomic_and_leaves_no_temporary_file(tmp_path: Path) -> None:
    """The write goes through a temporary sibling that must not survive the save."""
    source = _write(tmp_path, "a.c", _UNSPECIFIED)
    cache = MutationCache.load(source)
    cache.save()

    assert cache_path_for(source).is_file()
    assert not list(tmp_path.glob("*.tmp"))
