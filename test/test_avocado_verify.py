"""Tests for the per-function session budget in the avocado-verify harness.

These exercise `_should_grant_another_session` directly against synthetic
verification-attempts records, so no CBMC or `claude -p` invocation is needed.
"""

from __future__ import annotations

import json
from pathlib import Path

from avocado_verify import (
    _MIN_VERIFICATION_ATTEMPTS_PER_SESSION,
    ClaudeRun,
    GroundTruthVerificationResult,
    _latest_kill_score,
    _read_attempts,
    _should_grant_another_session,
)

from tools.run_cbmc import RunCbmcResult
from tools.util.callgraph import CallGraph


def _attempt(
    *,
    verified: bool = True,
    kill_score: float | None = None,
    survived: int = 0,
    spec_digest: str = "spec-1",
    function: str = "f",
) -> dict:
    """Build one verification-attempts record, with a mutation summary when scored."""
    record: dict = {
        "ts": "2026-09-03T00:00:00Z",
        "function": function,
        "file": "f.c",
        "verified": verified,
        "verdict": "PASS" if verified else "FAIL",
    }
    if kill_score is not None:
        record["mutation"] = {
            "kind": "mutation_summary",
            "killed": 1,
            "survived": survived,
            "kill_score": kill_score,
            "raw_kill_score": kill_score,
            "spec_digest": spec_digest,
        }
    return record


def _decide(attempts: list[dict], *, previous: int = 0, plateau_limit: int = 2):
    return _should_grant_another_session("f", attempts, previous, plateau_limit=plateau_limit)


def test_grants_a_session_when_the_agent_barely_tried() -> None:
    """A session that ran verification once has not yet earned a verdict on its usefulness."""
    decision = _decide([_attempt(kill_score=1.0)])

    assert decision.grant
    assert f"1/{_MIN_VERIFICATION_ATTEMPTS_PER_SESSION}" in decision.rationale


def test_counts_only_attempts_made_this_turn() -> None:
    """Attempts logged by an earlier function's session must not satisfy the floor."""
    attempts = [_attempt(kill_score=1.0), _attempt(kill_score=1.0), _attempt(kill_score=1.0)]

    # Two of the three predate this turn, so only one counts.
    decision = _decide(attempts, previous=2)

    assert decision.grant
    assert f"1/{_MIN_VERIFICATION_ATTEMPTS_PER_SESSION}" in decision.rationale


def test_stops_when_the_function_verified_with_no_mutants() -> None:
    """With nothing to mutate there is no kill score to raise, so another session is waste."""
    attempts = [_attempt(), _attempt()]

    decision = _decide(attempts)

    assert not decision.grant
    assert "no mutants" in decision.rationale


def test_stops_when_every_killable_mutant_is_accounted_for() -> None:
    """A verified function with no live survivors is finished."""
    attempts = [_attempt(kill_score=1.0, survived=0), _attempt(kill_score=1.0, survived=0)]

    decision = _decide(attempts)

    assert not decision.grant
    assert "accounted for" in decision.rationale


def test_grants_a_session_while_the_kill_score_is_still_rising() -> None:
    """This is the anti-give-up case: an improving agent keeps earning sessions."""
    attempts = [
        _attempt(kill_score=0.2, survived=4, spec_digest="spec-1"),
        _attempt(kill_score=0.6, survived=2, spec_digest="spec-2"),
    ]

    decision = _decide(attempts)

    assert decision.grant
    assert "live surviving mutant" in decision.rationale


def test_grants_a_session_when_the_specification_changed_without_moving_the_score() -> None:
    """A new specification is evidence of effort even if it has not paid off yet."""
    attempts = [
        _attempt(kill_score=0.5, survived=2, spec_digest="spec-1"),
        _attempt(kill_score=0.5, survived=2, spec_digest="spec-2"),
        _attempt(kill_score=0.5, survived=2, spec_digest="spec-3"),
    ]

    assert _decide(attempts).grant


def test_stops_on_a_plateau() -> None:
    """Same specification, same score, repeatedly: the agent is spinning."""
    attempts = [
        _attempt(kill_score=0.5, survived=2, spec_digest="spec-1"),
        _attempt(kill_score=0.5, survived=2, spec_digest="spec-1"),
        _attempt(kill_score=0.5, survived=2, spec_digest="spec-1"),
    ]

    decision = _decide(attempts)

    assert not decision.grant
    assert "unchanged" in decision.rationale


def test_plateau_needs_a_full_window_before_giving_up() -> None:
    """Two identical attempts are not yet a plateau when the limit is two."""
    attempts = [
        _attempt(kill_score=0.5, survived=2, spec_digest="spec-1"),
        _attempt(kill_score=0.5, survived=2, spec_digest="spec-1"),
    ]

    assert _decide(attempts, plateau_limit=2).grant
    # ...but with a stricter limit, it is.
    assert not _decide(attempts, plateau_limit=1).grant


def test_keeps_trying_while_the_function_does_not_verify() -> None:
    """An unverified function has no score to plateau on; more attempts are the way forward."""
    attempts = [
        _attempt(verified=False),
        _attempt(verified=False, spec_digest="spec-2"),
    ]

    decision = _decide(attempts)

    assert decision.grant
    assert "does not verify" in decision.rationale


def test_gives_up_on_a_function_that_never_verifies_and_never_changes() -> None:
    """Repeating an identical failing attempt is not progress."""
    attempts = [_attempt(verified=False) for _ in range(3)]

    decision = _decide(attempts)

    assert not decision.grant
    assert "still unverified" in decision.rationale


def test_a_newly_verifying_attempt_is_progress() -> None:
    """Going from FAIL to PASS breaks a plateau even at an unchanged score."""
    attempts = [
        _attempt(verified=False),
        _attempt(verified=False),
        _attempt(verified=True, kill_score=0.5, survived=1),
    ]

    assert _decide(attempts).grant


def test_a_usage_limited_session_is_not_retried(tmp_path: Path, monkeypatch) -> None:
    """Throttling is not a specification problem, so retrying only burns the session budget.

    Without this guard the raised session ceiling makes things *worse* than before: a usage-limited
    session logs no verification attempts, so the attempt floor would keep granting sessions that
    all fail the same way.
    """
    import avocado_verify

    source = tmp_path / "f.c"
    source.write_text("int f(void) { return 0; }\n", encoding="utf-8")

    sessions_run = 0

    def _fake_run_claude(_command: list[str], _timeout: int) -> ClaudeRun:
        nonlocal sessions_run
        sessions_run += 1
        return ClaudeRun(
            returncode=1,
            timed_out=False,
            is_error=True,
            session_id=None,
            result_text="Claude usage limit reached; resets 3pm",
            total_cost_usd=None,
            num_turns=None,
            duration_ms=None,
            subtype=None,
        )

    monkeypatch.setattr(avocado_verify, "_run_claude", _fake_run_claude)
    monkeypatch.setattr(
        avocado_verify,
        "run_cbmc",
        lambda *a, **k: RunCbmcResult(
            function="f", failed_step=None, timed_out=False, returncode=1, response=""
        ),
    )

    result = avocado_verify._verify_via_agent(
        "f",
        file_path=str(source),
        call_graph=CallGraph({"f": {"internal": [], "external": []}}),
        timeout=1,
        include_dirs=[],
    )

    assert sessions_run == 1
    assert result.agent_sessions == 1
    assert result.outcome is GroundTruthVerificationResult.USAGE_LIMITED


def test_read_attempts_filters_by_function_and_tolerates_junk(tmp_path: Path) -> None:
    """A malformed or foreign line must never break the budget decision."""
    log = tmp_path / "f-verification-attempts.jsonl"
    log.write_text(
        "\n".join(
            [
                json.dumps(_attempt(function="other")),
                "not json at all",
                "",
                json.dumps(_attempt(function="f", kill_score=0.25)),
            ]
        ),
        encoding="utf-8",
    )

    attempts = _read_attempts(log, "f")

    assert len(attempts) == 1
    assert _latest_kill_score(attempts) == 0.25


def test_read_attempts_returns_nothing_for_a_missing_log(tmp_path: Path) -> None:
    """A first run has no log yet; that is not an error."""
    assert _read_attempts(tmp_path / "absent.jsonl", "f") == []


def test_latest_kill_score_ignores_unscored_attempts() -> None:
    """A later attempt that never reached mutation testing must not erase a known score."""
    attempts = [_attempt(kill_score=0.75), _attempt(verified=False)]

    assert _latest_kill_score(attempts) == 0.75
    assert _latest_kill_score([_attempt(verified=False)]) is None
