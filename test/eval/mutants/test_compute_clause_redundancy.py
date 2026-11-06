"""Tests for `eval.mutants.compute_clause_redundancy`."""

import os
import shutil
from pathlib import Path

import pytest

from eval.mutants.compute_clause_redundancy import (
    CallerSideVerdict,
    ClauseRedundancyScore,
    _get_path_to_source_with_removed_clause,
    compute_clause_redundancy_score,
)
from eval.mutants.mutate_specification import CbmcClause
from eval.mutants.util import CLAUSE_DROP_INFIX, get_bounded_mutant_file_name, is_mutant_artifact

requires_cbmc = pytest.mark.skipif(
    not all(shutil.which(tool) for tool in ("goto-cc", "goto-instrument", "cbmc")),
    reason="requires goto-cc, goto-instrument, and cbmc on the PATH",
)

HEADER = "#define LIMIT 8\n"

# `clamp_three` is an in-file caller of `clamp`. It relies on `clamp`'s postcondition but not on
# its precondition, which every call site satisfies anyway.
SOURCE = """#include "lim.h"

int clamp(int x)
__CPROVER_requires(0 <= x)
__CPROVER_ensures(__CPROVER_return_value <= LIMIT)
{ return x < LIMIT ? x : LIMIT; }

int clamp_three(void)
__CPROVER_ensures(__CPROVER_return_value <= LIMIT)
{ return clamp(3); }
"""


def test_removed_clause_path_includes_function_name_so_functions_never_collide() -> None:
    workspace = Path("/work")
    source = Path("/src/foo.c")
    assert _get_path_to_source_with_removed_clause(workspace, source, "bar", 1) == Path(
        "/work/foo__clause_drop_bar_1.c"
    )
    # Two functions of one file get distinct paths for the same clause index.
    assert _get_path_to_source_with_removed_clause(workspace, source, "bar", 0) != (
        _get_path_to_source_with_removed_clause(workspace, source, "baz", 0)
    )
    # The name still matches the `*__clause_drop_*.c` pattern that `make clean-mutants` removes.
    assert (
        "__clause_drop_"
        in _get_path_to_source_with_removed_clause(workspace, source, "bar", 0).name
    )


def _assert_expected_clause_results(score: ClauseRedundancyScore | None) -> None:
    """Assert the per-clause results of scoring `clamp` in `SOURCE`.

    Args:
        score (ClauseRedundancyScore | None): The score to check.
    """
    assert score is not None
    assert score.total_clauses == 2
    assert {r.clause.clause_kind: (r.is_redundant, r.returncode) for r in score.results} == {
        CbmcClause.REQUIRES: (True, 0),
        CbmcClause.ENSURES: (True, 0),
    }
    assert {r.removed_clause.clause_kind: r.verdict for r in score.caller_side_results} == {
        CbmcClause.REQUIRES: CallerSideVerdict.REDUNDANT_FOR_CALLERS,
        CbmcClause.ENSURES: CallerSideVerdict.REQUIRED_BY_CALLERS,
    }
    assert score.unverifiable_baseline_callers == []


@requires_cbmc
def test_clause_redundancy_scoring_uses_the_include_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # CBMC writes its intermediate goto binaries to the working directory.
    monkeypatch.chdir(tmp_path)
    include_dir = tmp_path / "include"
    include_dir.mkdir()
    (include_dir / "lim.h").write_text(HEADER)
    source = tmp_path / "src" / "clamp.c"
    source.parent.mkdir()
    source.write_text(SOURCE)
    # Without the include directory the baseline does not compile, so there is no score.
    assert compute_clause_redundancy_score(str(source), "clamp") is None
    _assert_expected_clause_results(
        compute_clause_redundancy_score(str(source), "clamp", include_dirs=[str(include_dir)])
    )


def test_removed_clause_name_includes_label_so_labels_never_collide() -> None:
    assert get_bounded_mutant_file_name("foo", CLAUSE_DROP_INFIX, "bar", 1, ".c") == (
        "foo__clause_drop_bar_1.c"
    )
    # Two labels (functions or run tokens) get distinct names for the same clause index.
    assert get_bounded_mutant_file_name(
        "foo", CLAUSE_DROP_INFIX, "bar", 0, ".c"
    ) != get_bounded_mutant_file_name("foo", CLAUSE_DROP_INFIX, "baz", 0, ".c")
    # The name still matches the `*__clause_drop_*.c` pattern that `make clean-mutants` removes.
    name = get_bounded_mutant_file_name("foo", CLAUSE_DROP_INFIX, "bar", 0, ".c")
    assert "__clause_drop_" in name
    assert is_mutant_artifact(Path(name))


def test_removed_clause_name_is_bounded_for_long_names() -> None:
    stem = "s" * 240
    long_a = "f" * 200 + "a"
    long_b = "f" * 200 + "b"
    name_a = get_bounded_mutant_file_name(stem, CLAUSE_DROP_INFIX, long_a, 3, ".c")
    name_b = get_bounded_mutant_file_name(stem, CLAUSE_DROP_INFIX, long_b, 3, ".c")
    for name in (name_a, name_b):
        assert len(name.encode("utf-8")) <= 255
        assert "__clause_drop_" in name
        assert Path(name).suffix == ".c"
        assert is_mutant_artifact(Path(name))
    assert name_a != name_b
    assert name_a != get_bounded_mutant_file_name(stem, CLAUSE_DROP_INFIX, long_a, 4, ".c")


def test_removed_clause_name_handles_non_utf8_stems() -> None:
    # A basename containing a byte that is not valid UTF-8 decodes to a lone surrogate.
    short_name = get_bounded_mutant_file_name(
        os.fsdecode(b"f\xffoo"), CLAUSE_DROP_INFIX, "bar", 0, ".c"
    )
    assert os.fsencode(short_name) == b"f\xffoo__clause_drop_bar_0.c"

    long_name = get_bounded_mutant_file_name(
        os.fsdecode(b"\xff" * 240), CLAUSE_DROP_INFIX, "f" * 200, 0, ".c"
    )
    assert len(os.fsencode(long_name)) <= 255
    assert "__clause_drop_" in long_name


def test_removed_clause_name_does_not_split_multibyte_characters() -> None:
    name = get_bounded_mutant_file_name("é" * 120, CLAUSE_DROP_INFIX, "f" * 200, 0, ".c")
    encoded = os.fsencode(name)
    assert len(encoded) <= 255
    encoded.decode("utf-8")  # Raises if a multibyte character was split.
