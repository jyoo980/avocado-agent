"""Tests for `tools.util.session_state`: the one place that says what a merge rechecks."""

import shutil
from pathlib import Path

import pytest

from tools.run_cbmc import VACUOUS_VERDICT
from tools.util.session_state import (
    find_stray_files,
    is_vacuous_status,
    rechecked_on_change,
    vacuous_status,
)


def test_vacuous_status_carries_the_kind_of_vacuity_when_known() -> None:
    """The status names the kind of vacuity, and falls back to the bare verdict without one."""
    assert vacuous_status("unsatisfiable_preconditions") == "VACUOUS (unsatisfiable_preconditions)"
    assert vacuous_status(None) == VACUOUS_VERDICT
    assert vacuous_status("") == VACUOUS_VERDICT


def test_is_vacuous_status_reads_back_what_vacuous_status_builds() -> None:
    """Every status the builder makes is recognised, and other verdicts are not."""
    assert is_vacuous_status(vacuous_status("end_unreachable"))
    assert is_vacuous_status(vacuous_status(None))
    assert not is_vacuous_status("CBMC_FAILED")
    assert not is_vacuous_status("GOTO_CC_FAILED")
    assert not is_vacuous_status("")


def test_rechecked_on_change_is_the_verified_and_the_vacuous_functions() -> None:
    """A verified function and a vacuous one are rechecked; a failed one is not."""
    verified = frozenset({"quickSort"})
    unverified = {
        "partition": vacuous_status("unsatisfiable_preconditions"),
        "swap": "CBMC_FAILED",
        "main": "GOTO_CC_FAILED",
    }
    assert rechecked_on_change(verified, unverified) == {"quickSort", "partition"}


def test_rechecked_on_change_is_empty_when_nothing_is_verified_or_vacuous() -> None:
    """With no verified function and no vacuous proof there is nothing to recheck."""
    assert rechecked_on_change(set(), {"swap": "CBMC_FAILED"}) == set()
    assert rechecked_on_change(set(), {}) == set()


def _fork_with_symlinked_source(tmp_path: Path) -> tuple[Path, Path, Path, bytes]:
    """Return a canonical `foo.c -> foo_impl.c`, its session copy, the target, and the fork base.

    The session copy is made as a session's is, so `foo.c` and `foo_impl.c` in it are independent
    regular files.
    """
    canonical_dir = tmp_path / "canonical"
    canonical_dir.mkdir()
    target = canonical_dir / "foo_impl.c"
    target.write_text("int foo(void) { return 0; }\n", encoding="utf-8")
    canonical_file = canonical_dir / "foo.c"
    try:
        canonical_file.symlink_to(target.name)
    except OSError as e:
        pytest.skip(f"cannot create symlinks: {e}")
    fork_base = canonical_file.read_bytes()
    session_dir = tmp_path / "session" / "canonical"
    shutil.copytree(canonical_dir, session_dir)
    return canonical_file, session_dir / "foo.c", target, fork_base


def test_stray_files_ignore_a_merge_into_the_source_files_symlink_target(tmp_path: Path) -> None:
    """Another session's merge writes the target, but this session did not change its copy."""
    canonical_file, session_file, target, fork_base = _fork_with_symlinked_source(tmp_path)
    target.write_text("int foo(void) { return 1; }\n", encoding="utf-8")
    assert find_stray_files(session_file, canonical_file, fork_base) == []


def test_stray_files_report_an_edit_to_the_source_files_symlink_target(tmp_path: Path) -> None:
    """An edit to the session's copy of the target is not merged, so it is reported."""
    canonical_file, session_file, _target, fork_base = _fork_with_symlinked_source(tmp_path)
    (session_file.parent / "foo_impl.c").write_text(
        "int foo(void) { return 2; }\n", encoding="utf-8"
    )
    assert find_stray_files(session_file, canonical_file, fork_base) == ["modified:foo_impl.c"]
