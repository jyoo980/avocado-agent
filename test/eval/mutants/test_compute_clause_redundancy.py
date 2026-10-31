"""Tests for `eval.mutants.compute_clause_redundancy`."""

from pathlib import Path

from eval.mutants.compute_clause_redundancy import (
    _get_path_to_source_with_removed_clause,
    compute_clause_redundancy_score,
)

HEADER = "#define LIMIT 8\n"

SOURCE = """#include "lim.h"

int clamp(int x)
__CPROVER_requires(0 <= x)
__CPROVER_ensures(__CPROVER_return_value <= LIMIT)
{ return x < LIMIT ? x : LIMIT; }
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


def test_clause_redundancy_scoring_uses_the_include_directories(tmp_path: Path) -> None:
    include_dir = tmp_path / "include"
    include_dir.mkdir()
    (include_dir / "lim.h").write_text(HEADER)
    source = tmp_path / "src" / "clamp.c"
    source.parent.mkdir()
    source.write_text(SOURCE)
    # Without the include directory the baseline does not compile, so there is no score.
    assert compute_clause_redundancy_score(str(source), "clamp") is None
    assert compute_clause_redundancy_score(str(source), "clamp", include_dirs=[str(include_dir)])
