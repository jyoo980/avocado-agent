"""Tests for `tools.validate`: the checks an agent runs on its private copy before it is merged."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import avocado_verify
from avocado_verify import _fork_session
from tools import validate
from tools.run_cbmc import RunCbmcResult
from tools.util.cbmc_clause_stripper import strip_cbmc_clauses
from tools.util.edit_rules import function_body_offsets
from tools.util.session_state import (
    SESSION_FILE_NAME,
    SessionFileError,
    read_run_state,
    read_session_info,
    run_state_path,
    write_run_state,
)
from tools.validate import (
    EXIT_CANNOT_CHECK,
    EXIT_OK,
    EXIT_VIOLATIONS,
    check_conflicts,
    check_copy_edits,
    check_reverify,
)

_SWAP_CONTRACT = b"__CPROVER_requires(__CPROVER_w_ok(a, sizeof(int)))\n__CPROVER_assigns(*a, *b)"
_PARTITION_CONTRACT = (
    b"__CPROVER_requires(0 <= low && low <= high && high < 8)\n"
    b"__CPROVER_assigns(__CPROVER_object_whole(arr))"
)


class _FakeVerify:
    """A `verify_function` stand-in that fails the functions in `failing` and records calls."""

    def __init__(self, failing: set[str] | None = None) -> None:
        self.failing = failing or set()
        self.calls: list[str] = []

    def __call__(self, function: str, file_path: str, include_dirs=None) -> RunCbmcResult:
        del file_path, include_dirs
        self.calls.append(function)
        returncode = 10 if function in self.failing else 0
        return RunCbmcResult(
            function=function, failed_step=None, timed_out=False, returncode=returncode, response=""
        )


def _stripped_quicksort(tmp_path: Path) -> Path:
    source_dir = tmp_path / "bench"
    source_dir.mkdir()
    stripped, _ = strip_cbmc_clauses(Path("test/data/quicksort.c").read_bytes())
    canonical = source_dir / "quicksort.c"
    canonical.write_bytes(stripped)
    return canonical


def _write_contract(session_file: Path, function: str, contract: bytes) -> None:
    """Insert `contract` between `function`'s declarator and body in `session_file`."""
    source = session_file.read_bytes()
    bodies = function_body_offsets(source, function)
    assert len(bodies) == 1, bodies
    session_file.write_bytes(
        source[: bodies[0]].rstrip(b" \t") + b"\n" + contract + b"\n" + source[bodies[0] :]
    )


def _fake_goto_cc(monkeypatch, returncode: int | None, stderr: str = "") -> None:
    """Make `validate` see goto-cc on PATH, finishing with `returncode` (None: timing out)."""
    monkeypatch.setattr(validate.shutil, "which", lambda name: f"/usr/bin/{name}")
    result = (
        None
        if returncode is None
        else subprocess.CompletedProcess([], returncode, stdout="", stderr=stderr)
    )
    monkeypatch.setattr(validate, "run_goto_cc", lambda *a, **k: result)


@pytest.fixture
def swap_session(tmp_path: Path):
    """A session for `swap` over a contract-free quicksort; yields `(canonical, session)`."""
    canonical = _stripped_quicksort(tmp_path)
    session = _fork_session("swap", canonical)
    try:
        yield canonical, session
    finally:
        shutil.rmtree(session.directory, ignore_errors=True)


def test_fork_session_writes_a_session_file_the_tools_can_find(swap_session) -> None:
    canonical, session = swap_session
    info = read_session_info(session.file)
    assert info is not None
    assert info.function == "swap"
    assert info.canonical_file == canonical
    assert info.fork_base == canonical.read_bytes()
    # The session file sits beside the copied directory, so it is never a stray edit.
    assert {path.name for path in session.file.parent.iterdir()} == {"quicksort.c"}


def test_run_state_round_trips_and_is_left_out_of_session_copies(tmp_path: Path) -> None:
    canonical = _stripped_quicksort(tmp_path)
    assert read_run_state(canonical) is None
    write_run_state(canonical, verified={"swap"}, active={"partition"})
    state = read_run_state(canonical)
    assert state is not None
    assert state.verified == {"swap"} and state.active == {"partition"}
    session = _fork_session("partition", canonical)
    try:
        assert not run_state_path(session.file).exists()
    finally:
        shutil.rmtree(session.directory, ignore_errors=True)


def test_reverify_has_nothing_to_do_when_only_the_target_changed(swap_session, monkeypatch) -> None:
    canonical, session = swap_session
    fake = _FakeVerify()
    monkeypatch.setattr(validate, "verify_function", fake)
    write_run_state(canonical, verified={"partition", "quickSort"}, active={"swap"})
    _write_contract(session.file, "swap", _SWAP_CONTRACT)
    result = check_reverify("swap", session.file)
    assert result.exit_code == EXIT_OK and result.violations == []
    assert fake.calls == []


def test_reverify_passes_when_edited_functions_and_their_callers_still_verify(
    swap_session, monkeypatch
) -> None:
    canonical, session = swap_session
    original = canonical.read_bytes()
    fake = _FakeVerify()
    monkeypatch.setattr(validate, "verify_function", fake)
    write_run_state(canonical, verified={"partition", "quickSort"}, active={"swap"})
    _write_contract(session.file, "swap", _SWAP_CONTRACT)
    _write_contract(session.file, "partition", _PARTITION_CONTRACT)
    result = check_reverify("swap", session.file)
    assert result.exit_code == EXIT_OK and result.violations == []
    # The edited function and its verified caller are re-verified; the target is not.
    assert sorted(fake.calls) == ["partition", "quickSort"]
    # The check never writes to the canonical file.
    assert canonical.read_bytes() == original


def test_reverify_reports_a_verified_caller_that_stops_verifying(swap_session, monkeypatch) -> None:
    canonical, session = swap_session
    monkeypatch.setattr(validate, "verify_function", _FakeVerify(failing={"quickSort"}))
    write_run_state(canonical, verified={"partition", "quickSort"}, active={"swap"})
    _write_contract(session.file, "swap", _SWAP_CONTRACT)
    _write_contract(session.file, "partition", _PARTITION_CONTRACT)
    result = check_reverify("swap", session.file)
    assert result.exit_code == EXIT_VIOLATIONS
    assert len(result.violations) == 1
    violation = result.violations[0]
    assert violation.startswith("rule 9 (no broken verified specs): quickSort verified before")
    line = session.file.read_bytes().split(b"int partition")[0].count(b"\n") + 1
    assert f"your edits to partition (line {line})" in violation


def test_reverify_ignores_failures_of_functions_that_never_verified(
    swap_session, monkeypatch
) -> None:
    canonical, session = swap_session
    monkeypatch.setattr(validate, "verify_function", _FakeVerify(failing={"partition"}))
    write_run_state(canonical, verified=set(), active={"swap"})
    _write_contract(session.file, "partition", _PARTITION_CONTRACT)
    result = check_reverify("swap", session.file)
    assert result.exit_code == EXIT_OK and result.violations == []


def test_reverify_notes_an_edit_to_a_function_another_session_owns(
    swap_session, monkeypatch
) -> None:
    canonical, session = swap_session
    fake = _FakeVerify()
    monkeypatch.setattr(validate, "verify_function", fake)
    write_run_state(canonical, verified={"quickSort"}, active={"swap", "partition"})
    _write_contract(session.file, "partition", _PARTITION_CONTRACT)
    result = check_reverify("swap", session.file)
    assert result.exit_code == EXIT_OK and fake.calls == []
    assert any(
        "function:partition (a live session is working on it)" in note for note in result.notes
    )


def test_reverify_cannot_check_outside_a_session_or_without_run_state(
    tmp_path: Path, swap_session
) -> None:
    outside = tmp_path / "loose.c"
    outside.write_text("int f(void) { return 0; }\n")
    assert check_reverify("f", outside).exit_code == EXIT_CANNOT_CHECK
    _canonical, session = swap_session
    result = check_reverify("swap", session.file)
    assert result.exit_code == EXIT_CANNOT_CHECK
    assert "list of verified functions is not available" in result.render()


def test_edits_passes_a_contract_and_a_prefixed_helper(swap_session, monkeypatch) -> None:
    _canonical, session = swap_session
    _fake_goto_cc(monkeypatch, 0)
    _write_contract(session.file, "swap", _SWAP_CONTRACT)
    source = session.file.read_bytes().replace(
        b"void swap(", b"#define AVOCADO_SPEC_N 8\nvoid swap(", 1
    )
    session.file.write_bytes(source)
    result = check_copy_edits(session.file)
    assert result.exit_code == EXIT_OK and result.violations == []


def test_edits_reports_every_broken_rule_with_its_line(swap_session, monkeypatch) -> None:
    _canonical, session = swap_session
    _fake_goto_cc(monkeypatch, 1, stderr="quicksort.c:3: error: oops\n")
    source = session.file.read_bytes()
    source = source.replace(b"void swap(", b"#undef NULL\nvoid swap(", 1)
    source = source.replace(b"int t = *a;", b"int t = *a; t = t;", 1)
    session.file.write_bytes(source)
    (session.file.parent / "scratch.c").write_text("int x;\n")
    result = check_copy_edits(session.file)
    assert result.exit_code == EXIT_VIOLATIONS
    rules = [int(line.split()[1]) for line in result.violations]
    # Violations without a line come first, then the others in line order.
    assert rules == [6, 7, 4, 2], result.violations
    undef_line = source.split(b"#undef NULL")[0].count(b"\n") + 1
    assert any(f"line {undef_line}: new `#undef`" in line for line in result.violations)
    # goto-cc's own message is printed, so the agent need not run the verifier to see it.
    assert "    quicksort.c:3: error: oops" in result.notes


def test_edits_checks_the_sessions_own_function(swap_session, monkeypatch) -> None:
    _canonical, session = swap_session
    _fake_goto_cc(monkeypatch, 0)
    source = session.file.read_bytes()
    start = source.index(b"void swap(")
    end = source.index(b"}\n", start) + 2
    session.file.write_bytes(source[:start] + source[end:])
    result = check_copy_edits(session.file)
    assert result.exit_code == EXIT_VIOLATIONS
    assert any(line.startswith("rule 3 ") for line in result.violations), result.violations


@pytest.mark.parametrize(("which", "returncode"), [(None, 0), ("/usr/bin/goto-cc", None)])
def test_edits_cannot_check_when_goto_cc_does_not_run(
    swap_session, monkeypatch, which: str | None, returncode: int | None
) -> None:
    _canonical, session = swap_session
    _fake_goto_cc(monkeypatch, returncode)
    monkeypatch.setattr(validate.shutil, "which", lambda name: which)
    result = check_copy_edits(session.file)
    assert result.exit_code == EXIT_CANNOT_CHECK, result
    assert "rule 7 (the file compiles) is unchecked" in result.render()
    # The other rules are still checked, and their violations still reported.
    session.file.write_bytes(session.file.read_bytes().replace(b"int t = *a;", b"int t = 0;", 1))
    result = check_copy_edits(session.file)
    assert result.exit_code == EXIT_VIOLATIONS, result
    assert "rule 7 (the file compiles) is unchecked" in result.render()


def test_edits_cannot_check_outside_a_session(tmp_path: Path) -> None:
    outside = tmp_path / "loose.c"
    outside.write_text("int f(void) { return 0; }\n")
    assert check_copy_edits(outside).exit_code == EXIT_CANNOT_CHECK


def test_reverify_defers_to_edits_when_the_copy_breaks_the_edit_rules(
    swap_session, monkeypatch
) -> None:
    canonical, session = swap_session
    monkeypatch.setattr(validate, "verify_function", _FakeVerify())
    write_run_state(canonical, verified={"partition"}, active={"swap"})
    session.file.write_bytes(session.file.read_bytes().replace(b"int t = *a;", b"int t = *b;", 1))
    result = check_reverify("swap", session.file)
    assert result.exit_code == EXIT_CANNOT_CHECK
    assert "avocado-validate edits" in result.render()


def test_conflicts_is_clean_when_nothing_else_touched_what_the_session_edited(swap_session) -> None:
    canonical, session = swap_session
    write_run_state(canonical, verified=set(), active={"swap"})
    _write_contract(session.file, "swap", _SWAP_CONTRACT)
    _write_contract(session.file, "partition", _PARTITION_CONTRACT)
    result = check_conflicts("swap", session.file)
    assert result.exit_code == EXIT_OK and result.violations == []


def test_conflicts_allows_an_edit_to_a_function_a_live_session_only_calls(swap_session) -> None:
    canonical, session = swap_session
    # quickSort's session is live and calls partition; only quickSort itself is frozen.
    write_run_state(canonical, verified=set(), active={"swap", "quickSort"})
    _write_contract(session.file, "swap", _SWAP_CONTRACT)
    _write_contract(session.file, "partition", _PARTITION_CONTRACT)
    result = check_conflicts("swap", session.file)
    assert result.exit_code == EXIT_OK and result.violations == []


def test_conflicts_reports_an_edit_to_another_live_sessions_function(swap_session) -> None:
    canonical, session = swap_session
    write_run_state(canonical, verified=set(), active={"swap", "partition"})
    _write_contract(session.file, "swap", _SWAP_CONTRACT)
    _write_contract(session.file, "partition", _PARTITION_CONTRACT)
    result = check_conflicts("swap", session.file)
    assert result.exit_code == EXIT_VIOLATIONS and len(result.violations) == 1
    assert result.violations[0].startswith("rule 10 (no merge conflict): your edit to ")
    assert "function:partition (a live session is working on it)" in result.violations[0]


def test_conflicts_notes_a_callee_whose_contract_another_session_changed(tmp_path: Path) -> None:
    canonical = _stripped_quicksort(tmp_path)
    session = _fork_session("quickSort", canonical)
    try:
        write_run_state(canonical, verified={"partition"}, active={"quickSort"})
        # Another session's merge lands a contract on partition, which quickSort calls, and one
        # on swap, which it does not.
        _write_contract(canonical, "partition", _PARTITION_CONTRACT)
        _write_contract(canonical, "swap", _SWAP_CONTRACT)
        result = check_conflicts("quickSort", session.file)
        # A note, not a violation: nothing of the session's would be left out.
        assert result.exit_code == EXIT_OK and result.violations == []
        notes = [note for note in result.notes if note.startswith("note: ")]
        assert len(notes) == 1
        assert "changed the contract of partition, which quickSort calls" in notes[0]
        # The note carries the new contract, so the agent need not open the shared file.
        assert _PARTITION_CONTRACT.decode() in notes[0] and "int partition (" in notes[0]
        # Once the copy holds the new contract there is nothing left to say.
        _write_contract(session.file, "partition", _PARTITION_CONTRACT)
        result = check_conflicts("quickSort", session.file)
        assert result.exit_code == EXIT_OK
        assert not any(note.startswith("note: ") for note in result.notes)
    finally:
        shutil.rmtree(session.directory, ignore_errors=True)


def test_conflicts_reports_what_another_session_merged_meanwhile(swap_session) -> None:
    canonical, session = swap_session
    write_run_state(canonical, verified=set(), active={"swap"})
    _write_contract(session.file, "swap", _SWAP_CONTRACT)
    _write_contract(session.file, "partition", _PARTITION_CONTRACT)
    # Another session's merge lands a different contract on partition in the shared file.
    _write_contract(canonical, "partition", b"__CPROVER_requires(low <= high)")
    result = check_conflicts("swap", session.file)
    assert result.exit_code == EXIT_VIOLATIONS
    assert "function:partition (changed in the canonical file since the fork)" in result.render()
    # Without the conflicting edit, the copy is clean against the moved file.
    session.file.write_bytes(session.fork_base)
    _write_contract(session.file, "swap", _SWAP_CONTRACT)
    assert check_conflicts("swap", session.file).exit_code == EXIT_OK


def test_conflicts_reports_a_new_name_the_shared_file_now_defines_differently(swap_session) -> None:
    canonical, session = swap_session
    write_run_state(canonical, verified=set(), active={"swap"})
    source = session.file.read_bytes().replace(
        b"void swap(", b"#define AVOCADO_SPEC_N 8\nvoid swap(", 1
    )
    session.file.write_bytes(source)
    shared = canonical.read_bytes().replace(
        b"int partition", b"#define AVOCADO_SPEC_N 4\nint partition", 1
    )
    canonical.write_bytes(shared)
    result = check_conflicts("swap", session.file)
    assert result.exit_code == EXIT_VIOLATIONS
    assert "macro:AVOCADO_SPEC_N is already defined differently" in result.render()


def test_reverify_rechecks_a_vacuous_caller_but_does_not_count_it_as_broken(
    swap_session, monkeypatch
) -> None:
    canonical, session = swap_session
    fake = _FakeVerify(failing={"quickSort"})
    monkeypatch.setattr(validate, "verify_function", fake)
    write_run_state(
        canonical,
        verified={"partition"},
        active={"swap"},
        unverified={"quickSort": "VACUOUS (end_unreachable)"},
    )
    _write_contract(session.file, "partition", _PARTITION_CONTRACT)
    result = check_reverify("swap", session.file)
    assert sorted(fake.calls) == ["partition", "quickSort"]
    assert result.exit_code == EXIT_OK and result.violations == []


@pytest.mark.parametrize(
    "content",
    [
        "{not json",
        '{"function": null, "canonical_file": "/x.c", "fork_base": ".avocado-fork-base.c"}',
        "[]",
    ],
)
def test_edits_reports_a_damaged_session_file(swap_session, content: str) -> None:
    _canonical, session = swap_session
    (session.directory / SESSION_FILE_NAME).write_text(content, encoding="utf-8")
    with pytest.raises(SessionFileError):
        read_session_info(session.file)
    result = check_copy_edits(session.file)
    assert result.exit_code == EXIT_CANNOT_CHECK
    assert "is damaged" in result.render(), result


def test_edits_rejects_a_file_other_than_the_sessions_source_file(swap_session) -> None:
    _canonical, session = swap_session
    scratch = session.file.with_name("quicksort_try.c")
    scratch.write_bytes(session.file.read_bytes())
    result = check_copy_edits(scratch)
    assert result.exit_code == EXIT_CANNOT_CHECK
    assert f"pass --file {session.file.resolve()}" in result.render(), result
    nested = session.file.parent / "sub" / "quicksort.c"
    nested.parent.mkdir()
    nested.write_bytes(session.file.read_bytes())
    result = check_copy_edits(nested)
    assert result.exit_code == EXIT_CANNOT_CHECK
    assert f"there is no {SESSION_FILE_NAME}" in result.render(), result


def test_main_exits_cannot_check_on_an_internal_error(monkeypatch, capsys) -> None:
    def crash(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(validate, "check_copy_edits", crash)
    monkeypatch.setattr(sys, "argv", ["avocado-validate", "edits", "--file", "x.c"])
    with pytest.raises(SystemExit) as exit_info:
        validate.main()
    assert exit_info.value.code == EXIT_CANNOT_CHECK
    assert "cannot check: internal error" in capsys.readouterr().out


def test_fork_session_survives_a_failed_session_file_write(tmp_path: Path, monkeypatch) -> None:
    canonical = _stripped_quicksort(tmp_path)

    def fail(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(avocado_verify, "write_session_info", fail)
    session = _fork_session("swap", canonical)
    try:
        assert session.file.read_bytes() == canonical.read_bytes()
    finally:
        shutil.rmtree(session.directory, ignore_errors=True)
