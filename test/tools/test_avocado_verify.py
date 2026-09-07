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
from tools.util.contract_merge import find_function_span


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
    assert "edits to other functions, to other files and to headers are discarded" in prompt


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


def test_fork_session_copies_the_directory_outside_the_tree_without_artifacts(tmp_path: Path) -> None:
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
    span = find_function_span(source, function)
    assert span is not None
    session_file.write_bytes(
        source[: span.body_start_byte].rstrip(b" \t") + b"\n" + contract + b"\n" + source[span.body_start_byte :]
    )


def _log_attempts(session_file: Path, function: str, count: int = 2) -> None:
    """Pretend the agent ran the verifier `count` times on this copy."""
    log = session_file.with_name(f"{session_file.stem}-verification-attempts.jsonl")
    with log.open("a") as handle:
        for _ in range(count):
            handle.write(json.dumps({"function": function, "verified": True}) + "\n")


def _verified_cbmc(function: str) -> RunCbmcResult:
    return RunCbmcResult(function=function, failed_step=None, timed_out=False, returncode=0, response="ok")


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
            renamed = path.read_bytes().replace(function.encode(), b"renamed_" + function.encode(), 1)
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
            order, order=order, file_path=str(canonical), call_graph=graph, timeout=10,
            include_dirs=[], jobs=jobs, log_path=tmp_path / f"run{jobs}.jsonl", keep_sessions=False,
        )
        (s1, e1), (s2, e2) = (fake.intervals[f] for f in order[:2])
        overlap = s1 < e2 and s2 < e1
        assert overlap is expect_overlap, (jobs, fake.intervals)


def test_usage_limit_stops_new_submissions_but_in_flight_sessions_merge(tmp_path: Path, monkeypatch) -> None:
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
        order, order=order, file_path=str(canonical), call_graph=graph, timeout=10,
        include_dirs=[], jobs=2, log_path=tmp_path / "run.jsonl", keep_sessions=False,
    )
    assert limited == {order[0]}
    started = {result.function for result in results}
    assert order[0] in started and order[1] in started  # both were in flight
    assert order[2] not in started  # never submitted after the limit
    assert order[1] in fake.intervals  # the in-flight one finished and merged


def test_merge_failure_is_logged_and_canonical_untouched(tmp_path: Path, monkeypatch) -> None:
    canonical = _stripped_quicksort(tmp_path)
    original = canonical.read_bytes()
    monkeypatch.setattr(avocado_verify, "_run_claude", _fake_claude(rename={"swap"}))
    results, _ = _verify_functions(
        ["swap"], order=["swap", "partition", "quickSort"], file_path=str(canonical),
        call_graph=_quicksort_call_graph(), timeout=10, include_dirs=[], jobs=1,
        log_path=tmp_path / "run.jsonl", keep_sessions=False,
    )
    assert results[0].merge is not None and not results[0].merge.merged
    assert "swap" in results[0].merge.reason
    assert canonical.read_bytes() == original


def test_merge_and_verify_rejects_a_merge_that_does_not_compile(tmp_path: Path) -> None:
    canonical = _stripped_quicksort(tmp_path)
    original = canonical.read_bytes()
    session = _fork_session("swap", canonical)
    try:
        # A helper that includes a header that exists only in the session copy.
        (session.file.parent / "nope.h").write_text("int nope(void);\n")
        source = session.file.read_bytes().replace(
            b"void swap(", b'#include "nope.h"\nstatic int uses_nope(void) { return nope(); }\nvoid swap(', 1
        )
        session.file.write_bytes(source)
        report, cbmc = _merge_and_verify(session, canonical, include_dirs=[])
    finally:
        shutil.rmtree(session.directory, ignore_errors=True)
    assert not report.merged and "goto-cc" in report.reason
    assert canonical.read_bytes() == original
    assert cbmc.function == "swap"
