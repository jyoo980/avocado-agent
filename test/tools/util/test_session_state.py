"""Tests for `tools.util.session_state`: the one place that says what a merge rechecks."""

import shutil
from pathlib import Path

from tools.run_cbmc import VACUOUS_VERDICT
from tools.util.session_state import (
    MERGE_STAGING_SUFFIX,
    SNAPSHOT_IGNORE,
    find_stray_files,
    is_vacuous_status,
    rechecked_on_change,
    vacuous_status,
)


def _canonical_and_copy(tmp_path: Path) -> tuple[Path, Path]:
    canonical = tmp_path / "canonical" / "src"
    canonical.mkdir(parents=True)
    (canonical / "f.c").write_text("int f(void) { return 0; }\n")
    (canonical / "f.h").write_text("int f(void);\n")
    (canonical / "sub").mkdir()
    (canonical / "sub" / "g.h").write_text("int g(void);\n")
    copy = tmp_path / "session" / "src"
    shutil.copytree(canonical, copy, ignore=SNAPSHOT_IGNORE)
    return canonical, copy / "f.c"


def test_an_unchanged_copy_has_no_stray_files(tmp_path: Path) -> None:
    canonical, session_file = _canonical_and_copy(tmp_path)
    session_file.write_text("int f(void)\n__CPROVER_requires(1)\n{ return 0; }\n")
    assert find_stray_files(session_file, canonical) == []


def test_added_modified_and_deleted_files_are_stray(tmp_path: Path) -> None:
    canonical, session_file = _canonical_and_copy(tmp_path)
    copy = session_file.parent
    (copy / "new.c").write_text("int h;\n")
    (copy / "f.h").write_text("int f(void); int x;\n")
    (copy / "sub" / "g.h").unlink()
    assert find_stray_files(session_file, canonical) == [
        "modified:f.h",
        "added:new.c",
        "deleted:sub/g.h",
    ]


def test_files_inside_an_ignored_directory_are_not_stray(tmp_path: Path) -> None:
    canonical, session_file = _canonical_and_copy(tmp_path)
    git = session_file.parent / ".git"
    git.mkdir()
    (git / "config").write_text("[core]\n")
    (canonical / ".git").mkdir()
    (canonical / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    assert find_stray_files(session_file, canonical) == []


def test_the_staging_file_of_a_merge_is_not_a_deleted_file(tmp_path: Path) -> None:
    canonical, session_file = _canonical_and_copy(tmp_path)
    (canonical / f"f.c{MERGE_STAGING_SUFFIX}").write_text("int f(void) { return 0; }\n")
    assert find_stray_files(session_file, canonical) == []


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
