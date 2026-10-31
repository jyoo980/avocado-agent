"""Tests for `tools.util.mutation`."""

import shutil
from pathlib import Path

import pytest

from tools.util.mutation import MutationScore, generate_mutants_and_compute_score

INC_SOURCE = Path(__file__).resolve().parents[2] / "data" / "mutants" / "inc.c"


def test_mutation_testing_leaves_no_per_mutant_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    source = Path(shutil.copy(INC_SOURCE, source_dir))
    working_dir = tmp_path / "cwd"
    working_dir.mkdir()
    monkeypatch.chdir(working_dir)

    result = generate_mutants_and_compute_score(str(source), "inc")
    assert isinstance(result, MutationScore) and result.num_mutants > 0

    # Only the original source and the files that describe it remain.
    assert {path.name for path in source_dir.iterdir()} == {
        "inc.c",
        "inc-callgraph.json",
        "inc-cbmc-runs.jsonl",
    }
    # CBMC's intermediate `.goto` files are not written to the working directory.
    assert not list(working_dir.iterdir())
