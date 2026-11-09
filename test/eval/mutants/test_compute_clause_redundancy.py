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


def test_removed_clause_path_includes_function_name_so_functions_never_collide() -> None:
    source = Path("/src/foo.c")
    assert _get_path_to_source_with_removed_clause(source, "bar", 1) == Path(
        "/src/foo__clause_drop_bar_1.c"
    )
    # Two functions of one file get distinct paths for the same clause index.
    assert _get_path_to_source_with_removed_clause(source, "bar", 0) != (
        _get_path_to_source_with_removed_clause(source, "baz", 0)
    )
    # The name still matches the `*__clause_drop_*.c` pattern that `make clean-mutants` removes.
    assert "__clause_drop_" in _get_path_to_source_with_removed_clause(source, "bar", 0).name


def test_removed_clause_path_is_bounded_for_long_names() -> None:
    source = Path("/src/" + "s" * 240 + ".c")
    long_a = "f" * 200 + "a"
    long_b = "f" * 200 + "b"
    path_a = _get_path_to_source_with_removed_clause(source, long_a, 3)
    path_b = _get_path_to_source_with_removed_clause(source, long_b, 3)
    for path in (path_a, path_b):
        assert len(path.name.encode("utf-8")) <= 255
        assert "__clause_drop_" in path.name
        assert path.suffix == ".c"
    assert path_a != path_b
    assert path_a != _get_path_to_source_with_removed_clause(source, long_a, 4)


def test_removed_clause_path_handles_non_utf8_stems() -> None:
    # A basename containing a byte that is not valid UTF-8 decodes to a lone surrogate.
    short_source = Path(os.fsdecode(b"/src/f\xffoo.c"))
    short_path = _get_path_to_source_with_removed_clause(short_source, "bar", 0)
    assert os.fsencode(short_path.name) == b"f\xffoo__clause_drop_bar_0.c"

    long_source = Path(os.fsdecode(b"/src/" + b"\xff" * 240 + b".c"))
    long_path = _get_path_to_source_with_removed_clause(long_source, "f" * 200, 0)
    assert len(os.fsencode(long_path.name)) <= 255
    assert "__clause_drop_" in long_path.name


def test_removed_clause_path_does_not_split_multibyte_characters() -> None:
    source = Path("/src/" + "é" * 120 + ".c")
    path = _get_path_to_source_with_removed_clause(source, "f" * 200, 0)
    encoded = os.fsencode(path.name)
    assert len(encoded) <= 255
    encoded.decode("utf-8")  # Raises if a multibyte character was split.


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
def test_clause_redundancy_scoring_uses_the_include_directories(tmp_path: Path) -> None:
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


def test_removed_clause_path_of_a_symlink_is_named_for_its_target(tmp_path: Path) -> None:
    # If the file were named for `foo.c`, its path would be the target, and writing it would
    # overwrite the source.
    target = tmp_path / "foo__clause_drop_bar_0.c"
    target.write_text("int bar(void) { return 0; }\n", encoding="utf-8")
    source = tmp_path / "foo.c"
    try:
        source.symlink_to(target.name)
    except OSError as e:
        pytest.skip(f"cannot create symlinks: {e}")
    assert _get_path_to_source_with_removed_clause(source, "bar", 0) == (
        tmp_path / "foo__clause_drop_bar_0__clause_drop_bar_0.c"
    )
