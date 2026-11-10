"""Tests for `eval.mutants.util`."""

from pathlib import Path

import pytest

from eval.mutants.util import (
    get_bounded_mutant_file_name,
    get_files_with_extension,
    is_mutant_artifact,
    remove_files,
)


def test_is_mutant_artifact() -> None:
    assert is_mutant_artifact(Path("src/foo__mutant_bar_3.c"))
    assert is_mutant_artifact(Path("src/foo__clause_drop_bar_0.c"))
    # A name of a later attempt (see `create_mutant_file`).
    assert is_mutant_artifact(Path("src/foo__mutant_bar_3-1.c"))
    # A name without a function name.
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


def _make_files(directory: Path, names: list[str]) -> list[Path]:
    paths = [directory / name for name in names]
    for path in paths:
        path.write_text("")
    return paths


def _fail_to_unlink(monkeypatch: pytest.MonkeyPatch, undeletable: Path) -> None:
    original_unlink = Path.unlink

    def unlink(self: Path, missing_ok: bool = False) -> None:
        if self == undeletable:
            msg = f"cannot delete {self}"
            raise PermissionError(msg)
        original_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink)


def test_remove_files_deletes_files_and_skips_missing_ones(tmp_path: Path) -> None:
    paths = _make_files(tmp_path, ["a.c", "b.c"])
    remove_files([*paths, tmp_path / "missing.c"])
    assert not list(tmp_path.iterdir())


def test_remove_files_raises_after_attempting_every_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _make_files(tmp_path, ["a.c", "b.c", "c.c"])
    _fail_to_unlink(monkeypatch, paths[0])
    with pytest.raises(PermissionError):
        remove_files(paths)
    assert [path.name for path in tmp_path.iterdir()] == ["a.c"]


def test_remove_files_does_not_replace_a_propagating_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _make_files(tmp_path, ["a.c", "b.c"])
    _fail_to_unlink(monkeypatch, paths[0])

    def remove_files_while_raising() -> None:
        try:
            msg = "original"
            raise ValueError(msg)
        finally:
            remove_files(paths)

    with pytest.raises(ValueError, match="original"):
        remove_files_while_raising()
    assert [path.name for path in tmp_path.iterdir()] == ["a.c"]
