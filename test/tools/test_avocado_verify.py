"""Tests for `avocado_verify`'s decision whether to re-run a session."""

from avocado_verify import ClaudeRun, _should_rerun_session


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


def test_rerun_when_one_session_ended_short_of_the_floor() -> None:
    decision = _should_rerun_session([_session()], [1], improvable=True)
    assert decision.rerun
    assert "1 verification attempt(s)" in decision.rationale


def test_rerun_once_after_a_timeout_with_no_attempt_but_not_twice() -> None:
    first = _should_rerun_session([_session(timed_out=True)], [0], improvable=True)
    assert first.rerun
    assert "stopped by the harness" in first.rationale
    second = _should_rerun_session(
        [_session(timed_out=True), _session(timed_out=True)], [0, 0], improvable=True
    )
    assert not second.rerun
    assert "2 sessions in a row" in second.rationale


def test_a_session_that_finally_attempts_resets_the_no_attempt_stop() -> None:
    # Session 1 made no attempt, session 2 made one: the last session did try, so the
    # no-attempt rule does not apply and the ordinary floor decides.
    decision = _should_rerun_session(
        [_session(timed_out=True), _session()], [0, 1], improvable=True
    )
    assert decision.rerun


def test_no_rerun_after_a_usage_limit() -> None:
    decision = _should_rerun_session(
        [_session(is_error=True, text="Usage limit reached; resets at 3pm")], [0], improvable=True
    )
    assert not decision.rerun
    assert "usage limit" in decision.rationale


def test_no_rerun_when_floor_met_or_not_improvable_or_capped() -> None:
    assert not _should_rerun_session([_session()], [2], improvable=True).rerun
    assert not _should_rerun_session([_session()], [0], improvable=False).rerun
    capped = _should_rerun_session([_session()] * 3, [0, 0, 0], improvable=True)
    assert not capped.rerun
    assert "cap" in capped.rationale
