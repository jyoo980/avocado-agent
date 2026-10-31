"""Tests for `eval.mutants.compute_clause_redundancy`."""

from pathlib import Path

from eval.mutants.compute_clause_redundancy import compute_clause_redundancy_score

HEADER = "#define LIMIT 8\n"

SOURCE = """#include "lim.h"

int clamp(int x)
__CPROVER_requires(0 <= x)
__CPROVER_ensures(__CPROVER_return_value <= LIMIT)
{ return x < LIMIT ? x : LIMIT; }
"""


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
