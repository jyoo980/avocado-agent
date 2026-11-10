"""Tests for `eval.mutants.compute_clause_redundancy`."""

import os
import shutil
from functools import partial
from pathlib import Path

import pytest

from eval.mutants.compute_clause_redundancy import (
    CallerSideVerdict,
    ClauseRedundancyScore,
    _get_path_to_source_with_removed_clause,
    compute_clause_redundancy_score,
)
from eval.mutants.mutate_specification import CbmcClause
from eval.mutants.util import create_mutant_file
from tools.construct_call_graph import get_call_graph_path


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
    # Neither the removed-clause sources nor the call graphs cached for them are left behind.
    assert sorted(p.name for p in source.parent.iterdir()) == ["clamp-callgraph.json", "clamp.c"]


def test_removed_clause_path_leaves_room_for_the_call_graph_cache() -> None:
    source = Path("/src/" + "s" * 240 + ".c")
    for function in ("f", "f" * 200):
        path = _get_path_to_source_with_removed_clause(source, function, 0)
        assert len(os.fsencode(get_call_graph_path(path).name)) <= 255


def test_removed_clause_path_of_a_symlink_is_never_its_target(tmp_path: Path) -> None:
    # If the file were named for `foo.c` alone, its path would be the target, and writing it would
    # overwrite the source.
    target = tmp_path / "foo__clause_drop_bar_0.c"
    target.write_text("int bar(void) { return 0; }\n", encoding="utf-8")
    source = tmp_path / "foo.c"
    try:
        source.symlink_to(target.name)
    except OSError as e:
        pytest.skip(f"cannot create symlinks: {e}")
    assert _get_path_to_source_with_removed_clause(source, "bar", 0) == (
        tmp_path / "foo__foo__clause_drop_bar_0__clause_drop_bar_0.c"
    )


def test_removed_clause_paths_of_a_symlink_and_a_sibling_named_like_its_target_differ(
    tmp_path: Path,
) -> None:
    # `a.c -> x/foo.c` and a regular sibling `foo.c` may be scored concurrently; their
    # removed-clause sources must not overwrite one another.
    (tmp_path / "x").mkdir()
    target = tmp_path / "x" / "foo.c"
    target.write_text("int bar(void) { return 0; }\n", encoding="utf-8")
    sibling = tmp_path / "foo.c"
    sibling.write_text("int bar(void) { return 1; }\n", encoding="utf-8")
    source = tmp_path / "a.c"
    try:
        source.symlink_to(Path("x") / "foo.c")
    except OSError as e:
        pytest.skip(f"cannot create symlinks: {e}")
    assert _get_path_to_source_with_removed_clause(source, "bar", 0) == (
        tmp_path / "a__foo__clause_drop_bar_0.c"
    )
    assert _get_path_to_source_with_removed_clause(sibling, "bar", 0) == (
        tmp_path / "foo__clause_drop_bar_0.c"
    )


def test_create_mutant_file_never_overwrites_an_existing_file(tmp_path: Path) -> None:
    # `a.c -> foo.c` and a regular sibling `a__foo.c` have the same mutant names, as do two
    # concurrent runs on one file; whichever creates a name first keeps it.
    source = tmp_path / "a.c"
    try:
        source.symlink_to("foo.c")
    except OSError as e:
        pytest.skip(f"cannot create symlinks: {e}")
    sibling = tmp_path / "a__foo.c"
    taken = _get_path_to_source_with_removed_clause(sibling, "bar", 0)
    assert taken == _get_path_to_source_with_removed_clause(source, "bar", 0)
    taken.write_text("theirs", encoding="utf-8")

    path_for_attempt = partial(_get_path_to_source_with_removed_clause, source, "bar", 0)
    first = create_mutant_file(path_for_attempt, "mine")
    second = create_mutant_file(path_for_attempt, "mine again")
    assert first == tmp_path / "a__foo__clause_drop_bar_0-1.c"
    assert second == tmp_path / "a__foo__clause_drop_bar_0-2.c"
    assert taken.read_text(encoding="utf-8") == "theirs"
    assert first.read_text(encoding="utf-8") == "mine"
    assert second.read_text(encoding="utf-8") == "mine again"


def test_removed_clause_path_attempts_are_distinct_for_long_names() -> None:
    source = Path("/src/" + "s" * 240 + ".c")
    paths = {
        _get_path_to_source_with_removed_clause(source, "f" * 200, 0, attempt)
        for attempt in range(3)
    }
    assert len(paths) == 3
    for path in paths:
        assert len(path.name.encode("utf-8")) <= 255
        assert "__clause_drop_" in path.name
