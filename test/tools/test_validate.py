"""Tests for `tools.validate`: the checks an agent runs on its private copy before it is merged."""

import shutil
from pathlib import Path

import pytest

from avocado_verify import _fork_session
from tools import validate
from tools.util.cbmc_clause_stripper import strip_cbmc_clauses
from tools.util.edit_rules import function_body_offsets
from tools.util.session_state import read_session_info
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
    monkeypatch.setattr(validate, "compile_with_goto_cc", lambda *a, **k: 0)
    _write_contract(session.file, "swap", _SWAP_CONTRACT)
    source = session.file.read_bytes().replace(
        b"void swap(", b"#define AVOCADO_SPEC_N 8\nvoid swap(", 1
    )
    session.file.write_bytes(source)
    result = check_copy_edits("swap", session.file)
    assert result.exit_code == EXIT_OK and result.violations == []


def test_edits_reports_every_broken_rule_with_its_line(swap_session, monkeypatch) -> None:
    _canonical, session = swap_session
    monkeypatch.setattr(validate, "compile_with_goto_cc", lambda *a, **k: 1)
    source = session.file.read_bytes()
    source = source.replace(b"void swap(", b"#undef NULL\nvoid swap(", 1)
    source = source.replace(b"int t = *a;", b"int t = *a; t = t;", 1)
    session.file.write_bytes(source)
    (session.file.parent / "scratch.c").write_text("int x;\n")
    result = check_copy_edits("swap", session.file)
    assert result.exit_code == EXIT_VIOLATIONS
    rules = [int(line.split()[1]) for line in result.violations]
    assert sorted(rules) == [2, 4, 6, 7], result.violations
    undef_line = source.split(b"#undef NULL")[0].count(b"\n") + 1
    assert any(f"line {undef_line}: new `#undef`" in line for line in result.violations)


def test_edits_cannot_check_outside_a_session(tmp_path: Path) -> None:
    outside = tmp_path / "loose.c"
    outside.write_text("int f(void) { return 0; }\n")
    assert check_copy_edits("f", outside).exit_code == EXIT_CANNOT_CHECK
