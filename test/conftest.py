"""Fixtures shared across the test suite."""

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

_INC_SOURCE = Path(__file__).resolve().parent / "data" / "mutants" / "inc.c"


@pytest.fixture
def isolated_inc_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Yield a copy of `test/data/mutants/inc.c`, and afterward check that no stray files remain.

    The copy is alone in its directory, and the test runs in a separate, empty working directory.
    After the test, the copy's directory must contain only the copy, its call graph, and its CBMC
    run log, and the working directory must still be empty.

    Args:
        tmp_path (Path): A temporary directory that holds both directories.
        monkeypatch (pytest.MonkeyPatch): Used to change the working directory for the test.

    Yields:
        Path: The copy of `inc.c`, which defines the function `inc`.
    """
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    source = Path(shutil.copy(_INC_SOURCE, source_dir))
    working_dir = tmp_path / "cwd"
    working_dir.mkdir()
    monkeypatch.chdir(working_dir)

    yield source

    assert {path.name for path in source_dir.iterdir()} == {
        "inc.c",
        "inc-callgraph.json",
        "inc-cbmc-runs.jsonl",
    }
    # CBMC's intermediate `.goto` files are not written to the working directory.
    assert not list(working_dir.iterdir())
