"""Tests for `tools.validate`: the checks an agent runs on its private copy before it is merged."""

import shutil
from pathlib import Path

import pytest

from avocado_verify import _fork_session
from tools import validate
from tools.run_cbmc import RunCbmcResult
from tools.util.cbmc_clause_stripper import strip_cbmc_clauses
from tools.util.contract_merge import find_function_span
from tools.util.session_state import (
    read_run_state,
    read_session_info,
    run_state_path,
    write_run_state,
)
from tools.validate import EXIT_CANNOT_CHECK, EXIT_OK, EXIT_VIOLATIONS, check_reverify

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
    span = find_function_span(source, function)
    assert span is not None
    session_file.write_bytes(
        source[: span.body_start_byte].rstrip(b" \t")
        + b"\n"
        + contract
        + b"\n"
        + source[span.body_start_byte :]
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
    assert violation.startswith("no-broken-verified-specs: quickSort verified before")
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
    assert any("function:partition (a session is running on it)" in note for note in result.notes)


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
