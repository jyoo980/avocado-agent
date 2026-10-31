"""Tests for the per-function prompt built by `avocado_verify`."""

from avocado_verify import ClaudeRun, _build_claude_command, _build_prompt, _should_rerun_session
from tools.construct_call_graph import construct_call_graph
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


# --------------------------------------------------------------------------------------------------
# Concurrent sessions: readiness, private copies, merge-back, and the scheduler.
# --------------------------------------------------------------------------------------------------

import json
import re
import shutil
import tempfile
import threading
import time
from functools import partial
from pathlib import Path

import avocado_verify
from avocado_verify import (
    GroundTruthVerificationResult,
    _append_jsonl,
    _fork_session,
    _merge_and_verify,
    _ready_functions,
    _verify_functions,
)
from tools.run_cbmc import CbmcStep, RunCbmcResult
from tools.util.cbmc_clause_stripper import strip_cbmc_clauses
from tools.util.edit_rules import function_body_offsets, function_line
from tools.util.session_state import is_vacuous_status, rechecked_on_change
from tools.util.vacuity_check_result import Vacuity, VacuityCheckResult


def test_ready_functions_waits_only_for_earlier_callees() -> None:
    graph = CallGraph(
        {
            "swap": {"internal": [], "external": []},
            "partition": {"internal": ["swap"], "external": []},
            "quickSort": {"internal": ["partition", "quickSort"], "external": []},
            "even": {"internal": ["odd"], "external": []},  # mutual recursion
            "odd": {"internal": ["even"], "external": []},
        }
    )
    order = ["swap", "partition", "quickSort", "even", "odd"]
    # Nothing done: leaves and the first member of the cycle are ready; self-recursion is ignored.
    assert _ready_functions(order, graph, done=set(), active=set()) == ["swap", "even"]
    # `swap` running: `partition` still waits; `even` is ready and `odd` waits for `even`.
    assert _ready_functions(order, graph, done=set(), active={"swap"}) == ["even"]
    assert _ready_functions(order, graph, done={"swap", "even"}, active=set()) == [
        "partition",
        "odd",
    ]
    assert _ready_functions(order, graph, done={"swap", "partition"}, active=set()) == [
        "quickSort",
        "even",
    ]


def test_ready_functions_one_at_a_time_reproduces_the_topological_order() -> None:
    graph = _quicksort_call_graph()
    order = ["swap", "partition", "quickSort"]
    done: set[str] = set()
    sequence = []
    while len(done) < len(order):
        ready = _ready_functions(order, graph, done=done, active=set())
        assert ready, "the scheduler must never starve with work pending"
        sequence.append(ready[0])
        done.add(ready[0])
    assert sequence == order


def test_prompt_states_the_isolation_rule() -> None:
    prompt = _build_prompt(
        "swap", file_path="/tmp/copy/q.c", call_graph=_quicksort_call_graph(), include_dirs=[]
    )
    assert "private copy of the source directory" in prompt
    assert "A contract you fix on another function in this file is kept too" in prompt
    assert "Edits to other files and to headers are always discarded" in prompt
    assert "must stay exactly as it was" in prompt and "contract and body" not in prompt
    assert prompt.endswith("avocado-validate edits --function swap --file /tmp/copy/q.c")


def test_prompt_edits_command_carries_the_include_dirs() -> None:
    prompt = _build_prompt(
        "partition",
        file_path="/src/quicksort.c",
        call_graph=_quicksort_call_graph(),
        include_dirs=["/src/include"],
    )
    expected = "avocado-validate edits --function partition --file /src/quicksort.c"
    assert prompt.endswith(f"{expected} -I /src/include")
    assert "A copy that breaks an edit rule is not merged at all" in prompt


def test_append_jsonl_is_safe_under_threads(tmp_path: Path) -> None:
    log = tmp_path / "run.jsonl"

    def writer(index: int) -> None:
        for count in range(200):
            _append_jsonl(log, {"thread": index, "count": count, "pad": "x" * 500})

    threads = [threading.Thread(target=writer, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1600
    assert all(json.loads(line)["pad"] == "x" * 500 for line in lines)


def test_fork_session_copies_the_directory_outside_the_tree_without_artifacts(
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "bench"
    source_dir.mkdir()
    canonical = source_dir / "q.c"
    shutil.copy(Path("test/data/quicksort.c"), canonical)
    (source_dir / "q.h").write_text("int swap(int *, int *);\n")
    (source_dir / "q-verification-attempts.jsonl").write_text("{}\n")
    (source_dir / "q-callgraph.json").write_text("{}")
    (source_dir / "q__mutant_swap_0.c").write_text("int x;")
    session = _fork_session("swap", canonical)
    try:
        assert not session.directory.is_relative_to(tmp_path)
        assert session.file.read_bytes() == canonical.read_bytes() == session.fork_base
        assert (session.file.parent / "q.h").is_file()
        names = {path.name for path in session.file.parent.iterdir()}
        assert names == {"q.c", "q.h"}
    finally:
        shutil.rmtree(session.directory)


def _write_contract(session_file: Path, function: str, contract: bytes) -> None:
    """Insert `contract` between `function`'s declarator and body in `session_file`."""
    source = session_file.read_bytes()
    bodies = function_body_offsets(source, function)
    assert len(bodies) == 1, bodies
    session_file.write_bytes(
        source[: bodies[0]].rstrip(b" \t") + b"\n" + contract + b"\n" + source[bodies[0] :]
    )


def _log_attempts(session_file: Path, function: str, count: int = 2) -> None:
    """Pretend the agent ran the verifier `count` times on this copy."""
    log = session_file.with_name(f"{session_file.stem}-verification-attempts.jsonl")
    with log.open("a") as handle:
        for _ in range(count):
            handle.write(json.dumps({"function": function, "verified": True}) + "\n")


def _verified_cbmc(function: str) -> RunCbmcResult:
    return RunCbmcResult(
        function=function, failed_step=None, timed_out=False, returncode=0, response="ok"
    )


def _stripped_quicksort(tmp_path: Path) -> Path:
    source_dir = tmp_path / "bench"
    source_dir.mkdir()
    stripped, _ = strip_cbmc_clauses(Path("test/data/quicksort.c").read_bytes())
    canonical = source_dir / "quicksort.c"
    canonical.write_bytes(stripped)
    return canonical


_CONTRACTS = {
    "swap": b"__CPROVER_requires(__CPROVER_w_ok(a, sizeof(int)))\n__CPROVER_requires(__CPROVER_w_ok(b, sizeof(int)))\n"
    b"__CPROVER_assigns(*a, *b)\n__CPROVER_ensures(*a == __CPROVER_old(*b))\n__CPROVER_ensures(*b == __CPROVER_old(*a))",
    "partition": b"__CPROVER_requires(0 <= low && low <= high && high < 8)\n"
    b"__CPROVER_requires(__CPROVER_is_fresh(arr, 8 * sizeof(int)))\n__CPROVER_assigns(__CPROVER_object_whole(arr))",
    "quickSort": b"__CPROVER_requires(0 <= low && high < 8 && low <= high + 1)\n"
    b"__CPROVER_requires(__CPROVER_is_fresh(arr, 8 * sizeof(int)))\n__CPROVER_assigns(__CPROVER_object_whole(arr))",
}


class _FakeClaude:
    """A `_run_claude` stand-in that edits the session copy the way an agent would."""

    def __init__(
        self, sleep: float = 0.0, fail: set[str] | None = None, rename: set[str] | None = None
    ) -> None:
        self.sleep = sleep
        self.fail = fail or set()
        self.rename = rename or set()
        self.intervals: dict[str, tuple[float, float]] = {}

    def __call__(self, command: list[str], timeout: int) -> ClaudeRun:
        del timeout
        match = re.search(r"Verify (\w+) in (\S+?)\.\n", command[2])
        assert match, command[2][:80]
        function, path = match.group(1), Path(match.group(2))
        start = time.monotonic()
        time.sleep(self.sleep)
        if function in self.fail:
            return _session(is_error=True, text="Usage limit reached; resets at 3pm")
        if function in self.rename:
            renamed = path.read_bytes().replace(
                function.encode(), b"renamed_" + function.encode(), 1
            )
            path.write_bytes(renamed)
        else:
            _write_contract(path, function, _CONTRACTS.get(function, b"__CPROVER_ensures(1)"))
        _log_attempts(path, function)
        self.intervals[function] = (start, time.monotonic())
        return _session()


def _fake_claude(
    sleep: float = 0.0, fail: set[str] | None = None, rename: set[str] | None = None
) -> _FakeClaude:
    """Return a `_FakeClaude` (kept as a function so call sites read naturally)."""
    return _FakeClaude(sleep=sleep, fail=fail, rename=rename)


def _session_dirs() -> set[str]:
    return {p.name for p in Path(tempfile.gettempdir()).glob("avocado-session-*")}


def test_end_to_end_two_jobs_over_stripped_quicksort(tmp_path: Path, monkeypatch) -> None:
    canonical = _stripped_quicksort(tmp_path)
    before = _session_dirs()
    monkeypatch.setattr(avocado_verify, "_run_claude", _fake_claude())
    log = tmp_path / "run.jsonl"
    order = ["swap", "partition", "quickSort"]
    results, limited = _verify_functions(
        order,
        order=order,
        file_path=str(canonical),
        call_graph=_quicksort_call_graph(),
        timeout=10,
        include_dirs=[],
        jobs=2,
        log_path=log,
        keep_sessions=False,
    )
    assert not limited and len(results) == 3
    merged = canonical.read_bytes()
    for function, contract in _CONTRACTS.items():
        assert contract in merged, function
    by_name = {result.function: result for result in results}
    assert all(result.merge is not None and result.merge.merged for result in results)
    assert by_name["swap"].outcome is GroundTruthVerificationResult.VERIFIED  # real CBMC
    records = [json.loads(line) for line in log.read_text().splitlines()]
    assert {record["function"] for record in records} == set(order)
    assert all(record["merge"]["merged"] for record in records)
    assert _session_dirs() == before, "session copies must be cleaned up"


def test_two_independent_functions_overlap_only_with_two_jobs(tmp_path: Path, monkeypatch) -> None:
    source_dir = tmp_path / "bench"
    source_dir.mkdir()
    canonical = source_dir / "no_callees.c"
    shutil.copy(Path("test/data/no_callees.c"), canonical)
    graph = CallGraph(json.loads(Path(construct_call_graph(str(canonical))).read_text()))
    order = [f for f in graph if f != "main"]
    assert len(order) >= 2
    monkeypatch.setattr(avocado_verify, "verify_function", lambda f, *a, **k: _verified_cbmc(f))
    monkeypatch.setattr(avocado_verify, "compile_with_goto_cc", lambda *a, **k: 0)
    for jobs, expect_overlap in ((2, True), (1, False)):
        fake = _fake_claude(sleep=0.4)
        monkeypatch.setattr(avocado_verify, "_run_claude", fake)
        _verify_functions(
            order,
            order=order,
            file_path=str(canonical),
            call_graph=graph,
            timeout=10,
            include_dirs=[],
            jobs=jobs,
            log_path=tmp_path / f"run{jobs}.jsonl",
            keep_sessions=False,
        )
        (s1, e1), (s2, e2) = (fake.intervals[f] for f in order[:2])
        overlap = s1 < e2 and s2 < e1
        assert overlap is expect_overlap, (jobs, fake.intervals)


def test_usage_limit_stops_new_submissions_but_in_flight_sessions_merge(
    tmp_path: Path, monkeypatch
) -> None:
    source_dir = tmp_path / "bench"
    source_dir.mkdir()
    canonical = source_dir / "no_callees.c"
    shutil.copy(Path("test/data/no_callees.c"), canonical)
    graph = CallGraph(json.loads(Path(construct_call_graph(str(canonical))).read_text()))
    order = [f for f in graph if f != "main"]
    assert len(order) >= 3

    def ground_truth(function: str, *args, **kwargs) -> RunCbmcResult:
        # A CBMC pass outranks a usage limit in `_outcome_for`; the limited function's ground
        # truth must fail for the limit to surface, as it does for a function with no contract yet.
        if function == order[0]:
            return RunCbmcResult(function, CbmcStep.CBMC, False, 10, "failed")
        return _verified_cbmc(function)

    monkeypatch.setattr(avocado_verify, "verify_function", ground_truth)
    monkeypatch.setattr(avocado_verify, "compile_with_goto_cc", lambda *a, **k: 0)
    fake = _fake_claude(sleep=0.2, fail={order[0]})
    monkeypatch.setattr(avocado_verify, "_run_claude", fake)
    results, limited = _verify_functions(
        order,
        order=order,
        file_path=str(canonical),
        call_graph=graph,
        timeout=10,
        include_dirs=[],
        jobs=2,
        log_path=tmp_path / "run.jsonl",
        keep_sessions=False,
    )
    assert limited == {order[0]}
    started = {result.function for result in results}
    assert order[0] in started and order[1] in started  # both were in flight
    assert order[2] not in started  # never submitted after the limit
    assert order[1] in fake.intervals  # the in-progress one finished and merged


def test_merge_failure_is_logged_and_canonical_untouched(tmp_path: Path, monkeypatch) -> None:
    canonical = _stripped_quicksort(tmp_path)
    original = canonical.read_bytes()
    monkeypatch.setattr(avocado_verify, "_run_claude", _fake_claude(rename={"swap"}))
    results, _ = _verify_functions(
        ["swap"],
        order=["swap", "partition", "quickSort"],
        file_path=str(canonical),
        call_graph=_quicksort_call_graph(),
        timeout=10,
        include_dirs=[],
        jobs=1,
        log_path=tmp_path / "run.jsonl",
        keep_sessions=False,
    )
    assert results[0].merge is not None and not results[0].merge.merged
    # Renaming the function breaks the edit rules, and each broken rule is reported.
    assert "edit rule" in results[0].merge.reason
    violations = results[0].merge.violations
    assert any(v.startswith("rule 1 ") and "function swap" in v for v in violations), violations
    assert any(v.startswith("rule 3 ") and "swap is missing" in v for v in violations), violations
    assert canonical.read_bytes() == original


def test_merge_and_verify_rejects_a_merge_that_does_not_compile(tmp_path: Path) -> None:
    canonical = _stripped_quicksort(tmp_path)
    original = canonical.read_bytes()
    session = _fork_session("swap", canonical)
    try:
        # A helper that every edit rule allows but that the compiler rejects.
        source = session.file.read_bytes().replace(
            b"void swap(",
            b"static int avocado_spec_bad(void) { return no_such_variable; }\nvoid swap(",
            1,
        )
        session.file.write_bytes(source)
        report, cbmc, reverified = _merge_and_verify(session, canonical, include_dirs=[])
    finally:
        shutil.rmtree(session.directory, ignore_errors=True)
    assert not report.merged and "goto-cc" in report.reason and report.violations == []
    assert reverified == {}
    assert canonical.read_bytes() == original
    assert cbmc.function == "swap"


class _FakeVerify:
    """A `verify_function` stand-in that fails the functions named in `failing` and records calls."""

    def __init__(self, failing: set[str] | None = None) -> None:
        self.failing = failing or set()
        self.calls: list[str] = []

    def __call__(self, function: str, file_path: str, include_dirs=None) -> RunCbmcResult:
        del file_path, include_dirs
        self.calls.append(function)
        if function in self.failing:
            return RunCbmcResult(
                function=function, failed_step=None, timed_out=False, returncode=10, response="fail"
            )
        return _verified_cbmc(function)


def _call_graph_where_quicksort_also_calls_swap() -> CallGraph:
    """A quicksort call graph in which `swap` has a second caller besides `partition`."""
    return CallGraph(
        {
            "swap": {"internal": [], "external": []},
            "partition": {"internal": ["swap"], "external": []},
            "quickSort": {"internal": ["partition", "swap", "quickSort"], "external": []},
        }
    )


def _partition_session_that_fixes_swap(canonical: Path):
    """Fork a session for `partition` whose agent also put a contract on its callee `swap`."""
    session = _fork_session("partition", canonical)
    _write_contract(session.file, "partition", _CONTRACTS["partition"])
    _write_contract(session.file, "swap", _CONTRACTS["swap"])
    return session


def test_merge_and_verify_carries_a_callee_fix_and_reverifies_its_verified_callers(
    tmp_path: Path, monkeypatch
) -> None:
    canonical = _stripped_quicksort(tmp_path)
    fake = _FakeVerify()
    monkeypatch.setattr(avocado_verify, "verify_function", fake)
    state = avocado_verify._MergeState(active={"partition"}, verified={"swap", "quickSort"})
    session = _partition_session_that_fixes_swap(canonical)
    try:
        report, cbmc, reverified = _merge_and_verify(
            session,
            canonical,
            include_dirs=[],
            call_graph=_call_graph_where_quicksort_also_calls_swap(),
            state=state,
        )
    finally:
        shutil.rmtree(session.directory, ignore_errors=True)
    assert report.merged and report.carried == ["swap"], report
    merged = canonical.read_bytes()
    assert _CONTRACTS["partition"] in merged and _CONTRACTS["swap"] in merged
    # The carried callee and its other verified caller were re-verified; the merging function
    # itself is verified separately.
    assert set(reverified) == {"swap", "quickSort"}
    assert fake.calls[-1] == "partition" and set(fake.calls) == {"swap", "quickSort", "partition"}
    assert cbmc.is_function_verified
    assert state.active == set() and state.verified == {"swap", "quickSort", "partition"}


def test_merge_and_verify_reverts_carried_edits_that_break_a_verified_caller(
    tmp_path: Path, monkeypatch
) -> None:
    canonical = _stripped_quicksort(tmp_path)
    original = canonical.read_bytes()
    fake = _FakeVerify(failing={"quickSort"})
    monkeypatch.setattr(avocado_verify, "verify_function", fake)
    state = avocado_verify._MergeState(active={"partition"}, verified={"swap", "quickSort"})
    session = _partition_session_that_fixes_swap(canonical)
    try:
        report, _cbmc, reverified = _merge_and_verify(
            session,
            canonical,
            include_dirs=[],
            call_graph=_call_graph_where_quicksort_also_calls_swap(),
            state=state,
        )
    finally:
        shutil.rmtree(session.directory, ignore_errors=True)
    assert report.merged and report.carried == []
    assert any(
        "reverted" in entry and "quickSort would no longer verify" in entry
        for entry in report.dropped
    ), report.dropped
    merged = canonical.read_bytes()
    assert _CONTRACTS["partition"] in merged
    assert _CONTRACTS["swap"] not in merged
    assert function_line(merged, "swap") is not None
    assert not reverified["quickSort"].is_function_verified
    # Nothing that was verified before is lost.
    assert {"swap", "quickSort"} <= state.verified
    assert original.count(b"__CPROVER_") == 0 and merged.count(b"__CPROVER_ensures") == 0


def test_merge_drops_a_callee_edit_while_a_session_runs_on_the_callee(
    tmp_path: Path, monkeypatch
) -> None:
    canonical = _stripped_quicksort(tmp_path)
    fake = _FakeVerify()
    monkeypatch.setattr(avocado_verify, "verify_function", fake)
    state = avocado_verify._MergeState(active={"partition", "swap"}, verified=set())
    session = _partition_session_that_fixes_swap(canonical)
    try:
        report, _cbmc, reverified = _merge_and_verify(
            session, canonical, include_dirs=[], call_graph=_quicksort_call_graph(), state=state
        )
    finally:
        shutil.rmtree(session.directory, ignore_errors=True)
    assert report.merged and report.carried == [] and reverified == {}
    assert "function:swap (a live session is working on it)" in report.dropped
    assert _CONTRACTS["swap"] not in canonical.read_bytes()
    assert fake.calls == ["partition"]


def test_reverified_pass_counts_as_verified_for_recorded_functions_only(tmp_path: Path) -> None:
    log = tmp_path / "run.jsonl"
    _append_jsonl(log, {"function": "swap", "outcome": "UNVERIFIED"})
    _append_jsonl(
        log,
        {
            "function": "partition",
            "outcome": "VERIFIED",
            "reverified": {
                "swap": {"verdict": "PASS", "is_function_verified": True},
                "quickSort": {"verdict": "PASS", "is_function_verified": True},
            },
        },
    )
    outcomes = avocado_verify._read_function_outcomes(log)
    assert outcomes == {"swap": "VERIFIED", "partition": "VERIFIED"}


def test_two_overlapping_sessions_both_merge_into_the_canonical_file(
    tmp_path: Path, monkeypatch
) -> None:
    canonical = _stripped_quicksort(tmp_path)
    original = canonical.read_bytes()
    monkeypatch.setattr(avocado_verify, "verify_function", _FakeVerify())
    monkeypatch.setattr(avocado_verify, "compile_with_goto_cc", lambda *a, **k: 0)
    state = avocado_verify._MergeState(active={"swap", "quickSort"}, verified=set())
    # Both copies are taken before either session merges.
    first = _fork_session("swap", canonical)
    second = _fork_session("quickSort", canonical)
    try:
        _write_contract(first.file, "swap", _CONTRACTS["swap"])
        helper = b"#define AVOCADO_SPEC_BOUND 8\n"
        second.file.write_bytes(
            second.file.read_bytes().replace(b"void quickSort", helper + b"void quickSort", 1)
        )
        _write_contract(second.file, "quickSort", _CONTRACTS["quickSort"])
        graph = _quicksort_call_graph()
        report_first, _, _ = _merge_and_verify(
            first, canonical, include_dirs=[], call_graph=graph, state=state
        )
        # The canonical file has now moved on from the second session's starting point.
        assert canonical.read_bytes() != second.fork_base
        report_second, _, _ = _merge_and_verify(
            second, canonical, include_dirs=[], call_graph=graph, state=state
        )
    finally:
        shutil.rmtree(first.directory, ignore_errors=True)
        shutil.rmtree(second.directory, ignore_errors=True)
    assert report_first.merged and report_second.merged, (report_first, report_second)
    merged = canonical.read_bytes()
    assert _CONTRACTS["swap"] in merged and _CONTRACTS["quickSort"] in merged and helper in merged
    assert strip_cbmc_clauses(merged)[0].replace(helper, b"").split() == original.split()


def test_a_session_may_change_the_contract_of_a_function_a_live_session_only_calls(
    tmp_path: Path, monkeypatch
) -> None:
    canonical = _stripped_quicksort(tmp_path)
    fake = _FakeVerify()
    monkeypatch.setattr(avocado_verify, "verify_function", fake)
    monkeypatch.setattr(avocado_verify, "compile_with_goto_cc", lambda *a, **k: 0)
    # quickSort's session is live and calls partition; swap's session edits partition's contract.
    state = avocado_verify._MergeState(active={"swap", "quickSort"}, verified={"partition"})
    session = _fork_session("swap", canonical)
    try:
        _write_contract(session.file, "swap", _CONTRACTS["swap"])
        _write_contract(session.file, "partition", _CONTRACTS["partition"])
        report, _, reverified = _merge_and_verify(
            session, canonical, include_dirs=[], call_graph=_quicksort_call_graph(), state=state
        )
    finally:
        shutil.rmtree(session.directory, ignore_errors=True)
    # Only quickSort itself is frozen, so the edit to its callee is kept, and re-verified.
    assert report.merged and report.carried == ["partition"] and report.dropped == []
    assert list(reverified) == ["partition"]
    merged = canonical.read_bytes()
    assert _CONTRACTS["swap"] in merged and _CONTRACTS["partition"] in merged


def test_a_session_may_not_change_the_contract_of_another_live_sessions_function(
    tmp_path: Path, monkeypatch
) -> None:
    canonical = _stripped_quicksort(tmp_path)
    monkeypatch.setattr(avocado_verify, "verify_function", _FakeVerify())
    monkeypatch.setattr(avocado_verify, "compile_with_goto_cc", lambda *a, **k: 0)
    state = avocado_verify._MergeState(active={"swap", "partition"}, verified=set())
    session = _fork_session("swap", canonical)
    try:
        _write_contract(session.file, "swap", _CONTRACTS["swap"])
        _write_contract(session.file, "partition", _CONTRACTS["partition"])
        report, _, reverified = _merge_and_verify(
            session, canonical, include_dirs=[], call_graph=_quicksort_call_graph(), state=state
        )
    finally:
        shutil.rmtree(session.directory, ignore_errors=True)
    assert report.merged and report.carried == [] and reverified == {}
    assert report.dropped == ["function:partition (a live session is working on it)"]
    merged = canonical.read_bytes()
    assert _CONTRACTS["swap"] in merged and _CONTRACTS["partition"] not in merged


class _ClaudeThatEditsACallerToo:
    """A `_run_claude` stand-in: its first session for `swap` also edits `partition`."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def __call__(self, command: list[str], timeout: int) -> ClaudeRun:
        del timeout
        prompt = command[2]
        self.prompts.append(prompt)
        match = re.search(r"Verify (\w+) in (\S+?)\.\n", prompt)
        assert match, prompt[:80]
        path = Path(match.group(2))
        if len(self.prompts) == 1:
            _write_contract(path, "swap", _CONTRACTS["swap"])
            _write_contract(path, "partition", _CONTRACTS["partition"])
        _log_attempts(path, "swap")
        return _session()


def _run_swap_whose_edit_to_partition_is_left_out(tmp_path, monkeypatch, swap_verdicts):
    """Run `swap` while `partition` has a live session of its own; return what happened."""
    canonical = _stripped_quicksort(tmp_path)
    verdicts = iter(swap_verdicts)

    def ground_truth(function: str, file_path: str, include_dirs=None) -> RunCbmcResult:
        del file_path, include_dirs
        returncode = 0 if next(verdicts) else 10
        return RunCbmcResult(
            function=function, failed_step=None, timed_out=False, returncode=returncode, response=""
        )

    claude = _ClaudeThatEditsACallerToo()
    monkeypatch.setattr(avocado_verify, "_run_claude", claude)
    monkeypatch.setattr(avocado_verify, "verify_function", ground_truth)
    monkeypatch.setattr(avocado_verify, "compile_with_goto_cc", lambda *a, **k: 0)
    monkeypatch.setattr(
        avocado_verify, "is_spec_improvable_with_mutation_testing", lambda *a, **k: False
    )
    state = avocado_verify._MergeState(active={"swap", "partition"}, verified=set())
    result = avocado_verify._verify_via_agent(
        "swap",
        file_path=str(canonical),
        call_graph=_quicksort_call_graph(),
        timeout=10,
        include_dirs=[],
        state=state,
    )
    return canonical, claude, state, result


def test_a_function_broken_by_a_left_out_edit_gets_another_session_on_a_fresh_copy(
    tmp_path: Path, monkeypatch
) -> None:
    canonical, claude, state, result = _run_swap_whose_edit_to_partition_is_left_out(
        tmp_path, monkeypatch, swap_verdicts=[False, True]
    )
    assert result.merge_reruns == 1 and result.agent_sessions == 2
    assert result.outcome is avocado_verify.GroundTruthVerificationResult.VERIFIED
    # The second session is told what was left out, and starts from the merged contract.
    rerun = claude.prompts[1]
    assert rerun.startswith("This is a re-run: your contract for swap was merged")
    assert "function:partition (a live session is working on it)" in rerun
    merged = canonical.read_bytes()
    assert _CONTRACTS["swap"] in merged and _CONTRACTS["partition"] not in merged
    assert state.active == {"partition"} and "swap" in state.verified
    assert result.to_record()["merge_reruns"] == 1


def test_a_left_out_edit_that_breaks_nothing_does_not_cause_a_rerun(
    tmp_path: Path, monkeypatch
) -> None:
    _canonical, claude, _state, result = _run_swap_whose_edit_to_partition_is_left_out(
        tmp_path, monkeypatch, swap_verdicts=[True]
    )
    assert result.merge_reruns == 0 and len(claude.prompts) == 1
    assert result.merge is not None and result.merge.dropped


def test_a_rerun_is_not_repeated_when_nothing_more_is_left_out(tmp_path: Path, monkeypatch) -> None:
    _canonical, claude, _state, result = _run_swap_whose_edit_to_partition_is_left_out(
        tmp_path, monkeypatch, swap_verdicts=[False, False, False, False, False]
    )
    # The first merge leaves the edit out; later copies no longer contain it, so nothing is left
    # out again and the function is not re-run a second time.
    assert result.merge_reruns == 1 and len(claude.prompts) == 2
    assert result.outcome is avocado_verify.GroundTruthVerificationResult.UNVERIFIED


def test_no_rerun_after_left_out_edits_at_the_rerun_cap_or_a_usage_limit() -> None:
    report = avocado_verify.MergeReport(True, dropped=["function:partition (reason)"])
    failed = RunCbmcResult(
        function="swap", failed_step=None, timed_out=False, returncode=10, response=""
    )
    state = avocado_verify._MergeState()
    note = avocado_verify._note_for_rerun_after_merge
    failed_note = partial(note, report=report, cbmc=failed, state=state)
    assert failed_note("swap", sessions=[_session()])
    # The sessions run on one copy have their own cap; only merge re-runs count here.
    assert failed_note("swap", sessions=[_session()] * 5, merge_reruns=1)
    assert not failed_note("swap", sessions=[_session()], merge_reruns=2)
    limited = _session(is_error=True, text="Usage limit reached; resets at 3pm")
    assert not failed_note("swap", sessions=[limited])
    assert not note("swap", sessions=[_session()], report=report, cbmc=failed, state=None)
    assert not note(
        "swap", sessions=[_session()], report=report, cbmc=_verified_cbmc("swap"), state=state
    )


def test_a_callee_contract_changed_by_another_session_causes_a_rerun_only_on_failure() -> None:
    failed = RunCbmcResult(
        function="quickSort", failed_step=None, timed_out=False, returncode=10, response=""
    )
    report = avocado_verify.MergeReport(True, changed_since_fork=["partition", "swap"])
    note = partial(
        avocado_verify._note_for_rerun_after_merge,
        sessions=[_session()],
        state=avocado_verify._MergeState(),
        callees=["partition", "quickSort"],
    )
    text = note("quickSort", report=report, cbmc=failed)
    # Only the function it calls is named, and it may change that contract again.
    assert "changed the contract(s) of partition, which quickSort calls" in text
    assert "swap" not in text and "You may change a callee's contract" in text
    assert not note("quickSort", report=report, cbmc=_verified_cbmc("quickSort"))
    # A change to a function it does not call explains nothing.
    elsewhere = avocado_verify.MergeReport(True, changed_since_fork=["swap"])
    assert not note("quickSort", report=elsewhere, cbmc=failed)
    # A callee the session edited too is named once, as a left-out edit.
    both = avocado_verify.MergeReport(
        True,
        dropped=["function:partition (changed in the canonical file since the fork)"],
        changed_since_fork=["partition"],
    )
    text = note("quickSort", report=both, cbmc=failed)
    assert "left out these edits you made outside quickSort: function:partition" in text
    assert "which quickSort calls" not in text
    # The left-out edits are not assumed to be other functions' contracts.
    assert "your edits to other functions" not in text
    assert text.startswith("your contract for quickSort was merged into the shared file")


class _ClaudeForQuickSortWhilePartitionMoves:
    """A `_run_claude` stand-in for `quickSort`; another session merges into `partition` first."""

    def __init__(self, canonical: Path) -> None:
        self.canonical = canonical
        self.prompts: list[str] = []
        self.partition_seen: list[bool] = []

    def __call__(self, command: list[str], timeout: int) -> ClaudeRun:
        del timeout
        prompt = command[2]
        self.prompts.append(prompt)
        match = re.search(r"Verify (\w+) in (\S+?)\.\n", prompt)
        assert match, prompt[:80]
        path = Path(match.group(2))
        self.partition_seen.append(_CONTRACTS["partition"] in path.read_bytes())
        if len(self.prompts) == 1:
            _write_contract(path, "quickSort", _CONTRACTS["quickSort"])
            # Meanwhile another session's merge changes the contract of a function it calls.
            _write_contract(self.canonical, "partition", _CONTRACTS["partition"])
        _log_attempts(path, "quickSort")
        return _session()


def test_a_function_broken_by_another_sessions_edit_to_its_callee_gets_another_session(
    tmp_path: Path, monkeypatch
) -> None:
    canonical = _stripped_quicksort(tmp_path)
    verdicts = iter([False, True])

    def ground_truth(function: str, file_path: str, include_dirs=None) -> RunCbmcResult:
        del file_path, include_dirs
        returncode = 0 if next(verdicts) else 10
        return RunCbmcResult(
            function=function, failed_step=None, timed_out=False, returncode=returncode, response=""
        )

    claude = _ClaudeForQuickSortWhilePartitionMoves(canonical)
    monkeypatch.setattr(avocado_verify, "_run_claude", claude)
    monkeypatch.setattr(avocado_verify, "verify_function", ground_truth)
    monkeypatch.setattr(avocado_verify, "compile_with_goto_cc", lambda *a, **k: 0)
    monkeypatch.setattr(
        avocado_verify, "is_spec_improvable_with_mutation_testing", lambda *a, **k: False
    )
    state = avocado_verify._MergeState(active={"quickSort"}, verified={"partition"})
    result = avocado_verify._verify_via_agent(
        "quickSort",
        file_path=str(canonical),
        call_graph=_quicksort_call_graph(),
        timeout=10,
        include_dirs=[],
        state=state,
    )
    assert result.merge_reruns == 1 and result.agent_sessions == 2
    assert result.outcome is avocado_verify.GroundTruthVerificationResult.VERIFIED
    # Nothing of the session's was left out; the re-run is for the callee that moved.
    rerun = claude.prompts[1]
    assert rerun.startswith("This is a re-run: your contract for quickSort was merged")
    assert "changed the contract(s) of partition, which quickSort calls" in rerun
    # The first copy predates the other session's merge; the second one holds it.
    assert claude.partition_seen == [False, True]
    merged = canonical.read_bytes()
    assert _CONTRACTS["quickSort"] in merged and _CONTRACTS["partition"] in merged


def test_merge_reruns_stop_at_their_own_cap(tmp_path: Path, monkeypatch) -> None:
    canonical = _stripped_quicksort(tmp_path)

    def ground_truth(function: str, file_path: str, include_dirs=None) -> RunCbmcResult:
        del file_path, include_dirs
        return RunCbmcResult(
            function=function, failed_step=None, timed_out=False, returncode=10, response=""
        )

    class _ClaudeWhoseCalleeKeepsMoving:
        def __init__(self) -> None:
            self.sessions = 0

        def __call__(self, command: list[str], timeout: int) -> ClaudeRun:
            del timeout
            self.sessions += 1
            match = re.search(r"Verify (\w+) in (\S+?)\.\n", command[2])
            assert match
            path = Path(match.group(2))
            # Every session finds that partition's contract has been changed again meanwhile.
            _write_contract(canonical, "partition", b"__CPROVER_requires(%d > 0)" % self.sessions)
            _log_attempts(path, "quickSort")
            return _session()

    claude = _ClaudeWhoseCalleeKeepsMoving()
    monkeypatch.setattr(avocado_verify, "_run_claude", claude)
    monkeypatch.setattr(avocado_verify, "verify_function", ground_truth)
    monkeypatch.setattr(avocado_verify, "compile_with_goto_cc", lambda *a, **k: 0)
    monkeypatch.setattr(
        avocado_verify, "is_spec_improvable_with_mutation_testing", lambda *a, **k: False
    )
    state = avocado_verify._MergeState(active={"quickSort"}, verified=set())
    result = avocado_verify._verify_via_agent(
        "quickSort",
        file_path=str(canonical),
        call_graph=_quicksort_call_graph(),
        timeout=10,
        include_dirs=[],
        state=state,
    )
    assert result.merge_reruns == avocado_verify._MAX_MERGE_RERUNS_PER_FUNCTION == 2
    assert claude.sessions == 3
    assert result.outcome is avocado_verify.GroundTruthVerificationResult.UNVERIFIED


def test_a_callee_changed_during_the_ground_truth_run_is_verified_against_again(
    tmp_path: Path, monkeypatch
) -> None:
    canonical = _stripped_quicksort(tmp_path)
    seen: list[bool] = []

    def ground_truth(function: str, file_path: str, include_dirs=None) -> RunCbmcResult:
        del include_dirs
        has_new_contract = _CONTRACTS["partition"] in Path(file_path).read_bytes()
        seen.append(has_new_contract)
        if not has_new_contract:
            # While this run is under way, another session's merge lands on a callee.
            _write_contract(canonical, "partition", _CONTRACTS["partition"])
        # quickSort holds against the old contract of partition and not against the new one.
        return RunCbmcResult(
            function=function,
            failed_step=None,
            timed_out=False,
            returncode=10 if has_new_contract else 0,
            response="",
        )

    monkeypatch.setattr(avocado_verify, "verify_function", ground_truth)
    monkeypatch.setattr(avocado_verify, "compile_with_goto_cc", lambda *a, **k: 0)
    state = avocado_verify._MergeState(active={"quickSort"}, verified={"partition"})
    session = _fork_session("quickSort", canonical)
    try:
        _write_contract(session.file, "quickSort", _CONTRACTS["quickSort"])
        report, cbmc, _ = _merge_and_verify(
            session, canonical, include_dirs=[], call_graph=_quicksort_call_graph(), state=state
        )
    finally:
        shutil.rmtree(session.directory, ignore_errors=True)
    # The stale pass is not recorded: the function is verified again on the file as it now is.
    assert seen == [False, True]
    assert report.merged and report.changed_since_fork == ["partition"]
    assert not cbmc.is_function_verified and "quickSort" not in state.verified
    assert state.active == set()


def test_the_tools_logs_are_mirrored_beside_the_canonical_file(tmp_path: Path) -> None:
    canonical = _stripped_quicksort(tmp_path)
    session = _fork_session("swap", canonical)
    try:
        _log_attempts(session.file, "swap")
        avocado_verify._mirror_tool_logs(session, canonical)
        avocado_verify._mirror_tool_logs(session, canonical)
    finally:
        shutil.rmtree(session.directory, ignore_errors=True)
    attempts = canonical.with_name("quicksort-verification-attempts.jsonl")
    assert len(attempts.read_text().splitlines()) == 4


def test_run_claude_names_the_session_so_a_timed_out_one_can_be_found(monkeypatch) -> None:
    commands: list[list[str]] = []

    def times_out(command, **kwargs):
        commands.append(command)
        raise avocado_verify.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(avocado_verify.subprocess, "run", times_out)
    run = avocado_verify._run_claude(["claude", "--print", "prompt"], 7)
    assert run.timed_out and run.transcript_path is None
    assert commands[0][:3] == ["claude", "--print", "prompt"]
    assert commands[0][-2] == "--session-id" and commands[0][-1] == run.session_id
    # Every session gets an id of its own.
    assert avocado_verify._run_claude(["claude"], 7).session_id != run.session_id


def test_the_transcript_of_a_timed_out_session_is_kept_beside_the_canonical_file(
    tmp_path: Path, monkeypatch
) -> None:
    canonical = _stripped_quicksort(tmp_path)
    config_dir = tmp_path / "claude-config"
    project_dir = config_dir / "projects" / "-tmp-avocado-session-x-bench"
    project_dir.mkdir(parents=True)
    (project_dir / "timed-out-id.jsonl").write_text('{"type": "user"}\n')
    (project_dir / "sid.jsonl").write_text('{"type": "user"}\n')
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
    timed_out = avocado_verify.replace(_session(timed_out=True), session_id="timed-out-id")
    lost = avocado_verify.replace(_session(timed_out=True), session_id="no-such-id")
    finished = _session()
    kept = avocado_verify._keep_transcripts_of_timed_out_sessions(
        "partition", [timed_out, finished, lost], canonical
    )
    target = tmp_path / "bench" / "quicksort-avocado-transcripts" / "partition-timed-out-id.jsonl"
    assert target.read_text() == '{"type": "user"}\n'
    assert kept[0].transcript_path == str(target)
    # Only timed-out sessions are kept, and a transcript that cannot be found changes nothing.
    assert kept[1:] == [finished, lost]
    assert [path.name for path in target.parent.iterdir()] == ["partition-timed-out-id.jsonl"]
    # The kept transcripts are not copied into later sessions' private copies.
    session = _fork_session("swap", canonical)
    try:
        assert {path.name for path in session.file.parent.iterdir()} == {"quicksort.c"}
    finally:
        shutil.rmtree(session.directory, ignore_errors=True)


def test_prompt_names_the_callees_whose_contracts_are_not_verified() -> None:
    build = partial(
        _build_prompt,
        "quickSort",
        file_path="/src/quicksort.c",
        call_graph=_quicksort_call_graph(),
        include_dirs=[],
    )
    prompt = build(callee_status={"partition": "VACUOUS (unsatisfiable_preconditions)"})
    assert "verified): partition\n" in prompt and "verified earlier" not in prompt
    assert "NOT verified" in prompt
    assert "partition (VACUOUS (unsatisfiable_preconditions))" in prompt
    # With every callee verified there is nothing to warn about.
    assert "NOT verified" not in build(callee_status={})
    assert "NOT verified" not in build()


def _vacuous_cbmc(function: str) -> RunCbmcResult:
    return RunCbmcResult(
        function=function,
        failed_step=None,
        timed_out=False,
        returncode=0,
        response="vacuous",
        vacuity_check_result=VacuityCheckResult(Vacuity.UNSATISFIABLE_PRECONDITIONS, "")
    )


def test_merge_state_keeps_why_a_function_is_not_verified() -> None:
    state = avocado_verify._MergeState(verified={"partition"})
    state.record("partition", _vacuous_cbmc("partition"))
    failed = RunCbmcResult(
        function="swap", failed_step=CbmcStep.CBMC, timed_out=False, returncode=10, response=""
    )
    state.record("swap", failed)
    assert state.verified == set()
    assert state.unverified == {
        "partition": "VACUOUS (unsatisfiable_preconditions)",
        "swap": "CBMC_FAILED",
    }
    assert state.unverified_among(["swap", "quickSort"]) == {"swap": "CBMC_FAILED"}
    # A later pass, for instance after another session repaired the contract, clears it.
    state.record("swap", _verified_cbmc("swap"))
    assert state.verified == {"swap"} and "swap" not in state.unverified


def test_a_vacuous_contract_is_merged_but_its_function_is_not_verified(
    tmp_path: Path, monkeypatch
) -> None:
    canonical = _stripped_quicksort(tmp_path)
    monkeypatch.setattr(avocado_verify, "verify_function", lambda f, *a, **k: _vacuous_cbmc(f))
    monkeypatch.setattr(avocado_verify, "compile_with_goto_cc", lambda *a, **k: 0)
    state = avocado_verify._MergeState(active={"partition"}, verified=set())
    session = _fork_session("partition", canonical)
    try:
        _write_contract(session.file, "partition", _CONTRACTS["partition"])
        report, cbmc, _ = _merge_and_verify(
            session, canonical, include_dirs=[], call_graph=_quicksort_call_graph(), state=state
        )
    finally:
        shutil.rmtree(session.directory, ignore_errors=True)
    # Callers cannot be verified against a callee with no contract, so the contract stays.
    assert report.merged and _CONTRACTS["partition"] in canonical.read_bytes()
    assert str(cbmc) == "VACUOUS" and not cbmc.is_function_verified
    assert state.verified == set()
    assert state.unverified == {"partition": "VACUOUS (unsatisfiable_preconditions)"}


def test_a_function_verified_against_an_unverified_callee_says_so(
    tmp_path: Path, monkeypatch
) -> None:
    canonical = _stripped_quicksort(tmp_path)
    claude = _fake_claude()
    monkeypatch.setattr(avocado_verify, "_run_claude", claude)
    monkeypatch.setattr(avocado_verify, "verify_function", _FakeVerify())
    monkeypatch.setattr(avocado_verify, "compile_with_goto_cc", lambda *a, **k: 0)
    monkeypatch.setattr(
        avocado_verify, "is_spec_improvable_with_mutation_testing", lambda *a, **k: False
    )
    state = avocado_verify._MergeState(
        active={"quickSort"}, unverified={"partition": "VACUOUS (end_unreachable)"}
    )
    result = avocado_verify._verify_via_agent(
        "quickSort",
        file_path=str(canonical),
        call_graph=_quicksort_call_graph(),
        timeout=10,
        include_dirs=[],
        state=state,
    )
    assert result.outcome is avocado_verify.GroundTruthVerificationResult.VERIFIED
    assert result.unverified_callees == {"partition": "VACUOUS (end_unreachable)"}
    record = result.to_record()
    assert record["unverified_callees"] == {"partition": "VACUOUS (end_unreachable)"}
    assert record["cbmc"]["vacuity"] is None


def test_the_summary_counts_functions_verified_against_unverified_callees() -> None:
    assuming = avocado_verify._verified_assuming_unverified_callees
    order = ["swap", "partition", "quickSort"]
    graph = _quicksort_call_graph()
    assert assuming(order, {"swap", "partition", "quickSort"}, graph) == []
    # partition calls swap, which is not verified; quickSort's callees are.
    assert assuming(order, {"partition", "quickSort"}, graph) == ["partition"]
    # quickSort calls itself, which does not make it depend on an unverified callee.
    assert assuming(order, {"swap", "quickSort"}, graph) == ["quickSort"]
    assert assuming(order, {"partition"}, None) == []
    record = avocado_verify._run_summary_record(
        "completed",
        done=order,
        remaining=[],
        verified=2,
        total=3,
        verified_assuming_unverified_callees=["partition"],
    )
    assert record["verified"] == 2
    assert record["verified_assuming_unverified_callees"] == ["partition"]


def test_a_resumed_run_recalls_why_functions_were_not_verified(tmp_path: Path) -> None:
    log = tmp_path / "run.jsonl"
    _append_jsonl(
        log,
        {
            "function": "swap",
            "outcome": "UNVERIFIED",
            "cbmc": {"verdict": "VACUOUS", "vacuity": "end_unreachable"},
        },
    )
    _append_jsonl(
        log, {"function": "partition", "outcome": "UNVERIFIED", "cbmc": {"verdict": "CBMC_FAILED"}}
    )
    _append_jsonl(
        log, {"function": "quickSort", "outcome": "VERIFIED", "cbmc": {"verdict": "PASS"}}
    )
    _append_jsonl(log, {"function": "other", "outcome": "CLAUDE_TIMED_OUT"})
    assert avocado_verify._unverified_functions(log) == {
        "swap": "VACUOUS (end_unreachable)",
        "partition": "CBMC_FAILED",
        "other": "CLAUDE_TIMED_OUT",
    }


def test_a_vacuous_proof_counts_as_verified_in_the_final_tally_only() -> None:
    vacuous = _vacuous_cbmc("partition")
    assert not vacuous.is_function_verified
    assert avocado_verify._outcome_for(_session(), vacuous) is (
        avocado_verify.GroundTruthVerificationResult.VERIFIED
    )
    # Also when the session ran out of time; but a usage-limited session is retried instead.
    assert avocado_verify._outcome_for(_session(timed_out=True), vacuous) is (
        avocado_verify.GroundTruthVerificationResult.VERIFIED
    )
    limited = _session(is_error=True, text="Usage limit reached; resets at 3pm")
    assert avocado_verify._outcome_for(limited, vacuous) is (
        avocado_verify.GroundTruthVerificationResult.USAGE_LIMITED
    )
    assert avocado_verify._outcome_for(limited, _verified_cbmc("partition")) is (
        avocado_verify.GroundTruthVerificationResult.VERIFIED
    )
    no_contract = RunCbmcResult("swap", None, False, 0, "ok", no_contract=True)
    assert avocado_verify._outcome_for(_session(), no_contract) is (
        avocado_verify.GroundTruthVerificationResult.UNVERIFIED
    )
    # While the run is going, the vacuous function is still not trusted by its callers.
    state = avocado_verify._MergeState()
    state.record("partition", vacuous)
    assert "partition" not in state.verified and "partition" in state.unverified


def test_the_log_tells_a_vacuous_verified_function_from_a_real_one(tmp_path: Path) -> None:
    log = tmp_path / "run.jsonl"
    _append_jsonl(log, {"function": "swap", "outcome": "VERIFIED", "cbmc": {"verdict": "PASS"}})
    _append_jsonl(
        log,
        {
            "function": "partition",
            "outcome": "VERIFIED",
            "cbmc": {"verdict": "VACUOUS", "vacuity": "unsatisfiable_preconditions"},
        },
    )
    _append_jsonl(
        log, {"function": "other", "outcome": "UNVERIFIED", "cbmc": {"verdict": "NO_CONTRACT"}}
    )
    assert avocado_verify._verified_functions(log) == {"swap", "partition"}
    assert avocado_verify._vacuous_functions(log) == {"partition"}
    # A resumed run treats the vacuous function as not verified, with the reason.
    assert avocado_verify._unverified_functions(log) == {
        "partition": "VACUOUS (unsatisfiable_preconditions)",
        "other": "NO_CONTRACT",
    }
    # A later re-verification that passes for real clears it.
    _append_jsonl(
        log,
        {
            "function": "quickSort",
            "outcome": "VERIFIED",
            "cbmc": {"verdict": "PASS"},
            "reverified": {"partition": {"verdict": "PASS", "is_function_verified": True}},
        },
    )
    assert avocado_verify._vacuous_functions(log) == set()


def test_the_summary_counts_vacuous_proofs_as_verified_and_names_them(
    tmp_path: Path, monkeypatch
) -> None:
    log = tmp_path / "run.jsonl"
    _append_jsonl(log, {"function": "swap", "outcome": "VERIFIED", "cbmc": {"verdict": "PASS"}})
    _append_jsonl(
        log,
        {
            "function": "partition",
            "outcome": "VERIFIED",
            "cbmc": {"verdict": "VACUOUS", "vacuity": "end_unreachable"},
        },
    )
    _append_jsonl(
        log, {"function": "quickSort", "outcome": "VERIFIED", "cbmc": {"verdict": "PASS"}}
    )
    exits: list[int] = []
    monkeypatch.setattr(avocado_verify.sys, "exit", exits.append)
    avocado_verify._finalize_run(
        log,
        functions=["swap", "partition", "quickSort"],
        remaining=[],
        status="completed",
        stopped_early=False,
        call_graph=_quicksort_call_graph(),
    )
    summary = json.loads(log.read_text().splitlines()[-1])
    assert (summary["verified"], summary["total"]) == (3, 3)
    assert summary["vacuous"] == ["partition"]
    # quickSort verified against partition's vacuous contract, so its pass depends on it.
    assert summary["verified_assuming_unverified_callees"] == ["quickSort"]
    assert exits == [avocado_verify._EXIT_ALL_VERIFIED]


def test_a_vacuous_caller_is_reverified_when_a_merge_changes_its_callee(
    tmp_path: Path, monkeypatch
) -> None:
    canonical = _stripped_quicksort(tmp_path)
    # quickSort's proof was vacuous; partition's session now fixes swap, which quickSort calls,
    # and with the new contract quickSort fails.
    fake = _FakeVerify(failing={"quickSort"})
    monkeypatch.setattr(avocado_verify, "verify_function", fake)
    state = avocado_verify._MergeState(
        active={"partition"},
        verified={"swap"},
        unverified={"quickSort": "VACUOUS (end_unreachable)"},
    )
    assert state.rechecked_on_change() == {"swap", "quickSort"}
    session = _partition_session_that_fixes_swap(canonical)
    try:
        report, _cbmc, reverified = _merge_and_verify(
            session,
            canonical,
            include_dirs=[],
            call_graph=_call_graph_where_quicksort_also_calls_swap(),
            state=state,
        )
    finally:
        shutil.rmtree(session.directory, ignore_errors=True)
    assert set(reverified) == {"swap", "quickSort"}
    # Rule 9 protects only real proofs, so the edit is kept and quickSort's new verdict recorded.
    assert report.merged and report.carried == ["swap"] and report.dropped == []
    assert _CONTRACTS["swap"] in canonical.read_bytes()
    assert state.unverified["quickSort"] == "FAIL" and "quickSort" not in state.verified


def test_a_kept_reverification_updates_the_final_tally(tmp_path: Path) -> None:
    log = tmp_path / "run.jsonl"
    vacuous = {"verdict": "VACUOUS", "vacuity": "end_unreachable"}
    _append_jsonl(log, {"function": "quickSort", "outcome": "VERIFIED", "cbmc": vacuous})
    _append_jsonl(log, {"function": "swap", "outcome": "VERIFIED", "cbmc": {"verdict": "PASS"}})
    assert avocado_verify._vacuous_functions(log) == {"quickSort"}
    # A merge that was reverted (it carried nothing) does not change anyone's verdict.
    _append_jsonl(
        log,
        {
            "function": "partition",
            "outcome": "VERIFIED",
            "cbmc": {"verdict": "PASS"},
            "merge": {"carried": []},
            "reverified": {
                "quickSort": {"verdict": "CBMC_FAILED", "is_function_verified": False},
                "swap": {"verdict": "CBMC_FAILED", "is_function_verified": False},
            },
        },
    )
    assert avocado_verify._verified_functions(log) == {"quickSort", "swap", "partition"}
    # One that kept its edits does: the vacuous caller now fails and leaves the tally.
    _append_jsonl(
        log,
        {
            "function": "partition",
            "outcome": "VERIFIED",
            "cbmc": {"verdict": "PASS"},
            "merge": {"carried": ["swap"]},
            "reverified": {
                "quickSort": {"verdict": "CBMC_FAILED", "is_function_verified": False},
                "swap": {"verdict": "PASS", "is_function_verified": True},
            },
        },
    )
    assert avocado_verify._verified_functions(log) == {"swap", "partition"}
    assert avocado_verify._vacuous_functions(log) == set()
    assert avocado_verify._unverified_functions(log) == {"quickSort": "CBMC_FAILED"}


def test_status_of_a_vacuous_proof_is_recognised_by_the_shared_predicate() -> None:
    """The status the harness writes is the one `tools.util.session_state` recognises."""
    status = avocado_verify._status_of(_vacuous_cbmc("partition"))
    assert status == "VACUOUS (unsatisfiable_preconditions)"
    assert is_vacuous_status(status)
    assert rechecked_on_change(set(), {"partition": status}) == {"partition"}


def test_merge_state_rechecked_on_change_matches_the_shared_rule() -> None:
    """The harness's in-memory state applies the shared rule for what to recheck."""
    state = avocado_verify._MergeState(verified={"quickSort"})
    state.record("partition", _vacuous_cbmc("partition"))
    failed = RunCbmcResult(
        function="swap", failed_step=CbmcStep.CBMC, timed_out=False, returncode=10, response=""
    )
    state.record("swap", failed)
    assert state.rechecked_on_change() == {"quickSort", "partition"}
    assert state.rechecked_on_change() == rechecked_on_change(state.verified, state.unverified)


def test_a_pass_whose_body_the_depth_bound_keeps_out_stands_like_an_unknown_one() -> None:
    depth_limited = RunCbmcResult(
        function="partition",
        failed_step=None,
        timed_out=False,
        returncode=0,
        response="partition verified successfully",
        vacuity_check_result=VacuityCheckResult(Vacuity.DEPTH_LIMIT_REACHED, ""),
    )
    assert not depth_limited.is_vacuous and depth_limited.is_function_verified
    assert avocado_verify._outcome_for(_session(), depth_limited) is (
        avocado_verify.GroundTruthVerificationResult.VERIFIED
    )
    # Its callers may build on it, as on any other pass.
    state = avocado_verify._MergeState()
    state.record("partition", depth_limited)
    assert state.verified == {"partition"} and state.unverified == {}
