"""Tests for the per-function prompt built by `avocado_verify`."""

from avocado_verify import ClaudeRun, _build_claude_command, _build_prompt, _should_rerun_session
from tools.util.callgraph import CallGraph


def _quicksort_call_graph() -> CallGraph:
    return CallGraph(
        {
            "swap": {"internal": [], "external": []},
            "partition": {"internal": ["swap"], "external": []},
            "quickSort": {"internal": ["partition", "quickSort"], "external": []},
        }
    )


def test_prompt_names_the_tool_command_callees_and_callers() -> None:
    prompt = _build_prompt(
        "partition",
        file_path="/src/quicksort.c",
        call_graph=_quicksort_call_graph(),
        include_dirs=["/src/include"],
    )
    assert prompt.startswith("Verify partition in /src/quicksort.c.")
    assert "avocado-run-cbmc --function partition --file /src/quicksort.c -I /src/include" in prompt
    assert "callees" in prompt and ": swap" in prompt
    assert "callers" in prompt and ": quickSort" in prompt


def test_prompt_reports_none_for_leaf_functions_without_include_dirs() -> None:
    prompt = _build_prompt(
        "swap", file_path="/src/quicksort.c", call_graph=_quicksort_call_graph(), include_dirs=[]
    )
    assert "avocado-run-cbmc --function swap --file /src/quicksort.c\n" in prompt
    assert "-I" not in prompt
    assert "function is verified): none" in prompt
    assert "you write): partition" in prompt


def test_claude_command_leaves_auto_memory_enabled() -> None:
    # Memory across functions and runs is a deliberate feature of the harness; no `--settings`
    # override may switch it off.
    command = _build_claude_command("prompt", file_path="/src/q.c", include_dirs=["/src/inc"])
    assert "--settings" not in command
    assert command[:3] == ["claude", "--print", "prompt"]
    assert command.count("--add-dir") == 2


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


def test_retry_note_leads_the_prompt_and_attempts_rule_is_stated() -> None:
    prompt = _build_prompt(
        "swap",
        file_path="/src/q.c",
        call_graph=_quicksort_call_graph(),
        include_dirs=[],
        retry_note="the previous session ended after recording 0 verification attempt(s)",
    )
    assert prompt.startswith("This is a re-run: the previous session ended")
    assert "Only runs of this command against exactly this path are recorded" in prompt
    plain = _build_prompt(
        "swap", file_path="/src/q.c", call_graph=_quicksort_call_graph(), include_dirs=[]
    )
    assert not plain.startswith("This is a re-run")
