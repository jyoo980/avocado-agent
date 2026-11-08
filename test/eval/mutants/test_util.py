"""Tests for `eval.mutants.util`."""

from pathlib import Path

import pytest

from eval.mutants.util import (
    CLAUSE_DROP_INFIX,
    MUTANT_INFIX,
    get_files_with_extension,
    is_mutant_artifact,
    write_mutant_sources,
)


def test_write_mutant_sources_writes_next_to_the_source(tmp_path: Path) -> None:
    source = tmp_path / "foo.c"
    paths = write_mutant_sources(source, MUTANT_INFIX, ["int a;\n", "int b;\n"])
    assert [path.read_text(encoding="utf-8") for path in paths] == ["int a;\n", "int b;\n"]
    for index, path in enumerate(paths):
        assert path.parent == tmp_path
        assert path.name.startswith("foo__mutant_")
        assert path.name.endswith(f"_{index}.c")
        assert is_mutant_artifact(path)


def test_write_mutant_sources_uses_a_new_run_token_for_each_call(tmp_path: Path) -> None:
    source = tmp_path / "foo.c"
    first = write_mutant_sources(source, CLAUSE_DROP_INFIX, ["int a;\n"])
    second = write_mutant_sources(source, CLAUSE_DROP_INFIX, ["int a;\n"])
    assert first != second
    assert sorted(tmp_path.iterdir()) == sorted(first + second)


def test_write_mutant_sources_never_overwrites(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("secrets.token_hex", lambda _: "token")
    source = tmp_path / "foo.c"
    existing = tmp_path / "foo__mutant_token_1.c"
    existing.write_text("keep\n", encoding="utf-8")
    with pytest.raises(FileExistsError):
        write_mutant_sources(source, MUTANT_INFIX, ["int a;\n", "int b;\n"])
    # The existing file is untouched, and the file that the call created is removed.
    assert existing.read_text(encoding="utf-8") == "keep\n"
    assert list(tmp_path.iterdir()) == [existing]


def test_is_mutant_artifact() -> None:
    assert is_mutant_artifact(Path("src/foo__mutant_1a2b3c4d_3.c"))
    assert is_mutant_artifact(Path("src/foo__clause_drop_1a2b3c4d_0.c"))
    # A name that an older version wrote, without a label.
    assert is_mutant_artifact(Path("src/foo__mutant_3.c"))
    assert not is_mutant_artifact(Path("src/foo.c"))
    assert not is_mutant_artifact(Path("src/foo__mutant_notes.c"))


def test_get_files_with_extension_skips_mutants(tmp_path: Path) -> None:
    source = tmp_path / "foo.c"
    source.write_text("int a;\n", encoding="utf-8")
    write_mutant_sources(source, MUTANT_INFIX, ["int b;\n"])
    assert get_files_with_extension(str(tmp_path), ".c") == [source]
