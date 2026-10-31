"""Tests for `eval.mutants.compute_clause_redundancy`."""

import shutil
from pathlib import Path

import pytest

from eval.mutants.compute_clause_redundancy import compute_clause_redundancy_score

INC_SOURCE = Path(__file__).resolve().parents[2] / "data" / "mutants" / "inc.c"


def test_clause_redundancy_scoring_leaves_no_per_mutant_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    source = Path(shutil.copy(INC_SOURCE, source_dir))
    working_dir = tmp_path / "cwd"
    working_dir.mkdir()
    monkeypatch.chdir(working_dir)

    assert compute_clause_redundancy_score(str(source), "inc") is not None

    # Only the original source and the files that describe it remain.
    assert {path.name for path in source_dir.iterdir()} == {
        "inc.c",
        "inc-callgraph.json",
        "inc-cbmc-runs.jsonl",
    }
    # CBMC's intermediate `.goto` files are not written to the working directory.
    assert not list(working_dir.iterdir())
