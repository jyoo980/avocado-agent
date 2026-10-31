"""Tests for `eval.mutants.compute_clause_redundancy`."""

import shutil
from pathlib import Path

import pytest

from eval.mutants.compute_clause_redundancy import (
    CallerSideVerdict,
    ClauseRedundancyScore,
    compute_clause_redundancy_score,
)
from eval.mutants.mutate_specification import CbmcClause

pytestmark = pytest.mark.skipif(
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


def test_mutants_outside_the_source_directory_resolve_quoted_includes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "src" / "clamp.c"
    source.parent.mkdir()
    (source.parent / "lim.h").write_text(HEADER)
    source.write_text(SOURCE)
    workspace = tmp_path / "workspace"
    _assert_expected_clause_results(
        compute_clause_redundancy_score(str(source), "clamp", workspace=workspace)
    )
