"""Tests for mutation-score aggregation and the section reported back to the agent."""

from __future__ import annotations

from eval.mutants.mutate_function import Mutant

from tools.util.mutation import (
    _MAX_MUTATION_SECTION_CHARS,
    MutantVerificationResult,
    _aggregate_mutation_score,
    get_mutation_testing_results_for_client,
)

_SOURCE = "int f(int a, int b) {\n  return a < b;\n}\n"


def _mutant(replacement: str = "<=") -> Mutant:
    return Mutant(
        function="f",
        operator_class="RELATIONAL",
        original_operator="<",
        replacement_operator=replacement,
        start_byte=_SOURCE.index("<"),
        end_byte=_SOURCE.index("<") + len(replacement),
        line=2,
        column=11,
        mutant_source=_SOURCE.replace("<", replacement),
    )


def _killed(mutant_id: str = "") -> MutantVerificationResult:
    return MutantVerificationResult(
        mutant=_mutant(), path_to_mutant="m.c", killed=True, returncode=10, mutant_id=mutant_id
    )


def _survivor(mutant_id: str = "abc123") -> MutantVerificationResult:
    return MutantVerificationResult(
        mutant=_mutant(), path_to_mutant="m.c", killed=False, returncode=0, mutant_id=mutant_id
    )


def _presumed_equivalent(reason: str = "presumed_equivalent") -> MutantVerificationResult:
    return MutantVerificationResult(
        mutant=_mutant(),
        path_to_mutant="",
        killed=False,
        returncode=0,
        presumed_equivalent=True,
        mutant_id="eq00001",
        skip_reason=reason,
        skip_detail="survived 3 distinct specifications",
    )


def test_presumed_equivalent_mutants_leave_the_kill_score_denominator() -> None:
    """Excluding a presumed-equivalent mutant raises the adjusted score but not the raw one."""
    results = [_killed(), _killed(), _killed(), _survivor(), _presumed_equivalent()]

    score = _aggregate_mutation_score(results, "f.c", "f")

    # Adjusted: 3 killed of 4 decided. Raw: 3 killed of 5, counting the equivalent as a survivor.
    assert score.num_killed == 3
    assert score.num_survived == 1
    assert score.num_presumed_equivalent == 1
    assert score.kill_score == 0.75
    assert score.raw_kill_score == 0.6


def test_aggregate_counts_reused_verdicts() -> None:
    """Replayed verdicts are counted so the speedup is visible rather than silent."""
    reused = MutantVerificationResult(
        mutant=_mutant(),
        path_to_mutant="",
        killed=True,
        returncode=10,
        mutant_id="cached01",
        skip_reason="memoized",
    )
    results = [reused, _killed(), _survivor()]

    score = _aggregate_mutation_score(results, "f.c", "f")

    assert score.num_reused_from_cache == 1


def test_undecided_mutants_are_still_excluded_from_both_scores() -> None:
    """Timed-out and compile-failed mutants remain outside both denominators."""
    timed_out = MutantVerificationResult(
        mutant=_mutant(), path_to_mutant="m.c", killed=False, returncode=124, timed_out=True
    )
    compile_failed = MutantVerificationResult(
        mutant=_mutant(), path_to_mutant="m.c", killed=False, returncode=1, compile_failed=True
    )

    score = _aggregate_mutation_score([_killed(), timed_out, compile_failed], "f.c", "f")

    assert score.num_survived == 0
    assert score.kill_score == 1.0
    assert score.raw_kill_score == 1.0


def test_section_reports_one_score_when_nothing_was_presumed_equivalent() -> None:
    """With no equivalence judgements the historical single-line format is preserved."""
    score = _aggregate_mutation_score([_killed(), _survivor()], "quicksort.c", "f")

    section = get_mutation_testing_results_for_client(score)

    assert "Mutation kill score: 0.5000 (killed 1/2; 1 survived" in section
    assert "Raw kill score" not in section


def test_section_reports_both_scores_when_a_mutant_was_presumed_equivalent() -> None:
    """The agent sees the adjusted score it should optimize and the comparable raw score."""
    score = _aggregate_mutation_score(
        [_killed(), _survivor(), _presumed_equivalent()], "quicksort.c", "f"
    )

    section = get_mutation_testing_results_for_client(score)

    assert "Mutation kill score: 0.5000 (adjusted; excludes 1 presumed-equivalent mutant(s))" in (
        section
    )
    assert "Raw kill score: 0.3333" in section
    assert "1 presumed-equivalent" in section
    assert "presumed unkillable" in section


def test_section_reports_agent_declarations_separately() -> None:
    """A declaration made by an agent is attributed as such."""
    score = _aggregate_mutation_score(
        [_killed(), _presumed_equivalent(reason="agent_declared")], "quicksort.c", "f"
    )

    section = get_mutation_testing_results_for_client(score)

    assert "(1 declared by an agent)" in section


def test_section_names_each_survivor_with_its_stable_id() -> None:
    """The id is what an agent passes to `avocado-mark-equivalent`, so it must be shown."""
    score = _aggregate_mutation_score([_survivor("feedface")], "quicksort.c", "f")

    section = get_mutation_testing_results_for_client(score)

    assert "[id feedface]" in section
    assert "avocado-mark-equivalent" in section


def test_section_omits_the_equivalence_advice_when_nothing_survived() -> None:
    """There is no point telling an agent to strengthen a spec that killed everything."""
    score = _aggregate_mutation_score([_killed()], "quicksort.c", "f")

    section = get_mutation_testing_results_for_client(score)

    assert "All decided mutants were killed." in section
    assert "avocado-mark-equivalent" not in section


def test_section_stays_within_budget_including_its_trailing_advice() -> None:
    """The advice is counted against the cap, not appended after it."""
    big = "x" * 8_000
    survivors = []
    for index in range(40):
        mutant = Mutant(
            function="f",
            operator_class="RELATIONAL",
            original_operator="<",
            replacement_operator="<=",
            start_byte=1,
            end_byte=3,
            line=index + 1,
            column=0,
            # A large body makes each survivor's unified diff big enough to exhaust the budget.
            mutant_source=f"int f(void) {{ /* {big} */ return 0 <= 1; }}",
        )
        survivors.append(
            MutantVerificationResult(
                mutant=mutant,
                path_to_mutant="m.c",
                killed=False,
                returncode=0,
                mutant_id=f"id{index:04d}",
            )
        )

    section = get_mutation_testing_results_for_client(
        _aggregate_mutation_score(survivors, "big.c", "f")
    )

    assert "more surviving mutant(s) omitted" in section
    assert len(section) <= _MAX_MUTATION_SECTION_CHARS
    # The advice survives truncation rather than being what pushed the section over.
    assert "avocado-mark-equivalent" in section
