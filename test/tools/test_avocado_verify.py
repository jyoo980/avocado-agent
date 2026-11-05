"""Tests for `avocado_verify`'s decision whether to re-run a session."""

import pytest

from avocado_verify import ClaudeRun, _RerunDecision, _should_rerun_session


def _session(*, timed_out: bool = False, is_error: bool = False, text: str = "") -> ClaudeRun:
    return ClaudeRun(
        returncode=124 if timed_out else (1 if is_error else 0),
        timed_out=timed_out,
        is_error=is_error or timed_out,
        session_id=None if timed_out else "sid",
        result_text=text,
        total_cost_usd=None,
        num_turns=None,
        duration_ms=None,
        subtype=None,
    )


def _decide(
    sessions: list[ClaudeRun],
    attempts: list[int],
    *,
    file_attempts: list[int] | None = None,
    verified: bool = False,
    timeout: int = 1800,
) -> _RerunDecision:
    return _should_rerun_session(
        sessions,
        attempts,
        file_attempts_after_each_session=attempts if file_attempts is None else file_attempts,
        verified=verified,
        timeout=timeout,
    )


def test_rerun_when_one_session_ended_short_of_the_floor() -> None:
    decision = _decide([_session()], [1])
    assert decision.rerun
    assert "1 verification attempt(s)" in decision.rationale


def test_rerun_once_after_a_timeout_with_no_attempt_but_not_twice() -> None:
    first = _decide([_session(timed_out=True)], [0])
    assert first.rerun
    assert "stopped by the harness" in first.rationale
    second = _decide([_session(timed_out=True), _session(timed_out=True)], [0, 0])
    assert not second.rerun
    assert "2 sessions in a row" in second.rationale


def test_a_session_that_finally_attempts_resets_the_no_attempt_stop() -> None:
    # Session 1 made no attempt, session 2 made one: the last session did try, so the
    # no-attempt rule does not apply and the ordinary floor decides.
    decision = _decide([_session(timed_out=True), _session()], [0, 1])
    assert decision.rerun


def test_no_rerun_after_a_usage_limit() -> None:
    decision = _decide([_session(is_error=True, text="Usage limit reached; resets at 3pm")], [0])
    assert not decision.rerun
    assert "usage limit" in decision.rationale


def test_no_rerun_when_floor_met_or_verified_or_capped() -> None:
    assert not _decide([_session()], [2]).rerun
    assert not _decide([_session()], [0], verified=True).rerun
    capped = _decide([_session()] * 3, [0, 0, 0])
    assert not capped.rerun
    assert "cap" in capped.rationale


def test_the_timeout_in_the_rationale_is_the_configured_one() -> None:
    decision = _decide([_session(timed_out=True)], [0], timeout=600)
    assert "after 600 s" in decision.rationale


def test_attempts_on_callees_prevent_the_no_attempt_stop() -> None:
    # Neither session attempted the function itself, but both verified a callee in the file.
    decision = _decide([_session(), _session()], [0, 0], file_attempts=[1, 2])
    assert decision.rerun


def test_meeting_the_floor_is_explained() -> None:
    decision = _decide([_session()], [2])
    assert not decision.rerun
    assert "2 verification attempt(s)" in decision.rationale


def test_the_rationale_reports_the_attempts_over_the_turn() -> None:
    decision = _decide([_session(), _session()], [1, 1], file_attempts=[1, 2])
    assert decision.rerun
    assert "recording 0 verification attempt(s)" in decision.rationale
    assert "for 1 in 2 session(s)" in decision.rationale


def test_the_no_attempt_stop_counts_only_consecutive_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("avocado_verify._MAX_AGENT_SESSIONS_PER_FUNCTION", 4)
    # Sessions 1 and 3 made no attempt, but session 2 did, so they are not in a row.
    assert _decide([_session()] * 3, [0, 1, 1]).rerun
    stopped = _decide([_session()] * 3, [1, 1, 1])
    assert not stopped.rerun
    assert "2 sessions in a row" in stopped.rationale


def test_a_usage_limit_on_the_last_allowed_session_is_reported() -> None:
    sessions = [_session(timed_out=True)] * 2 + [
        _session(is_error=True, text="Usage limit reached")
    ]
    decision = _decide(sessions, [0, 0, 0], file_attempts=[0, 1, 1])
    assert not decision.rerun
    assert "usage limit" in decision.rationale


def test_a_verifying_specification_is_explained() -> None:
    decision = _decide([_session()], [0], verified=True)
    assert not decision.rerun
    assert "verifies" in decision.rationale
