"""Tests for `tools.validate`: the checks an agent runs on its private copy before it is merged."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import avocado_verify
from avocado_verify import _fork_session
from tools import validate
from tools.util.cbmc_clause_stripper import strip_cbmc_clauses
from tools.util.edit_rules import function_body_offsets
from tools.util.session_state import SESSION_FILE_NAME, SessionFileError, read_session_info
from tools.validate import EXIT_CANNOT_CHECK, EXIT_OK, EXIT_VIOLATIONS, check_copy_edits

_SWAP_CONTRACT = b"__CPROVER_requires(__CPROVER_w_ok(a, sizeof(int)))\n__CPROVER_assigns(*a, *b)"


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


def test_edits_cannot_check_when_goto_cc_cannot_be_started(swap_session, monkeypatch) -> None:
    _canonical, session = swap_session
    monkeypatch.setattr(validate.shutil, "which", lambda name: f"/usr/bin/{name}")

    def no_slot(*args, **kwargs):
        msg = "no subprocess slot"
        raise PermissionError(msg)

    monkeypatch.setattr(validate, "run_goto_cc", no_slot)
    result = check_copy_edits(session.file)
    assert result.exit_code == EXIT_CANNOT_CHECK, result
    assert "goto-cc could not be run: no subprocess slot" in result.render()


def test_edits_cannot_check_outside_a_session(tmp_path: Path) -> None:
    outside = tmp_path / "loose.c"
    outside.write_text("int f(void) { return 0; }\n")
    assert check_copy_edits(outside).exit_code == EXIT_CANNOT_CHECK


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
