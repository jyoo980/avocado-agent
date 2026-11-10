"""Tests for `eval.mutants.util`."""

from pathlib import Path

from eval.mutants.util import (
    get_bounded_mutant_file_name,
    get_files_with_extension,
    is_mutant_artifact,
)


def test_is_mutant_artifact() -> None:
    assert is_mutant_artifact(Path("src/foo__mutant_bar_3.c"))
    assert is_mutant_artifact(Path("src/foo__clause_drop_bar_0.c"))
    # A name of a later attempt (see `create_mutant_file`).
    assert is_mutant_artifact(Path("src/foo__mutant_bar_3-1.c"))
    # A name that an older version wrote, without a function name.
    assert is_mutant_artifact(Path("src/foo__mutant_3.c"))
    assert not is_mutant_artifact(Path("src/foo.c"))
    assert not is_mutant_artifact(Path("src/foo__mutant_notes.c"))


def test_is_mutant_artifact_recognizes_bounded_names() -> None:
    for name in [
        get_bounded_mutant_file_name("foo", "clause_drop", "bar", 0, ".c"),
        get_bounded_mutant_file_name("s" * 240, "mutant", "f" * 200, 3, ".c", attempt=2),
    ]:
        assert is_mutant_artifact(Path(name))


def test_get_files_with_extension_skips_mutants(tmp_path: Path) -> None:
    source = tmp_path / "foo.c"
    source.write_text("int a;\n", encoding="utf-8")
    (tmp_path / "foo__mutant_bar_0.c").write_text("int b;\n", encoding="utf-8")
    (tmp_path / "foo__clause_drop_bar_0-1.c").write_text("int c;\n", encoding="utf-8")
    assert get_files_with_extension(str(tmp_path), ".c") == [source]
