"""Tests for `eval.mutants.util`."""

import os
from pathlib import Path

import pytest

from eval.mutants.util import (
    CLAUSE_DROP_INFIX,
    MUTANT_INFIX,
    get_bounded_mutant_file_name,
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


def test_bounded_mutant_file_name_includes_label_so_labels_never_collide() -> None:
    assert get_bounded_mutant_file_name("foo", CLAUSE_DROP_INFIX, "bar", 1, ".c") == (
        "foo__clause_drop_bar_1.c"
    )
    # Two labels (functions or run tokens) get distinct names for the same clause index.
    assert get_bounded_mutant_file_name(
        "foo", CLAUSE_DROP_INFIX, "bar", 0, ".c"
    ) != get_bounded_mutant_file_name("foo", CLAUSE_DROP_INFIX, "baz", 0, ".c")
    # The name still matches the `*__clause_drop_*.c` pattern that `make clean-mutants` removes.
    name = get_bounded_mutant_file_name("foo", CLAUSE_DROP_INFIX, "bar", 0, ".c")
    assert "__clause_drop_" in name
    assert is_mutant_artifact(Path(name))


def test_bounded_mutant_file_name_is_bounded_for_long_names() -> None:
    stem = "s" * 240
    long_a = "f" * 200 + "a"
    long_b = "f" * 200 + "b"
    name_a = get_bounded_mutant_file_name(stem, CLAUSE_DROP_INFIX, long_a, 3, ".c")
    name_b = get_bounded_mutant_file_name(stem, CLAUSE_DROP_INFIX, long_b, 3, ".c")
    for name in (name_a, name_b):
        assert len(name.encode("utf-8")) <= 255
        assert "__clause_drop_" in name
        assert Path(name).suffix == ".c"
        assert is_mutant_artifact(Path(name))
    assert name_a != name_b
    assert name_a != get_bounded_mutant_file_name(stem, CLAUSE_DROP_INFIX, long_a, 4, ".c")


def test_bounded_mutant_file_name_handles_non_utf8_stems() -> None:
    # A basename containing a byte that is not valid UTF-8 decodes to a lone surrogate.
    short_name = get_bounded_mutant_file_name(
        os.fsdecode(b"f\xffoo"), CLAUSE_DROP_INFIX, "bar", 0, ".c"
    )
    assert os.fsencode(short_name) == b"f\xffoo__clause_drop_bar_0.c"

    long_name = get_bounded_mutant_file_name(
        os.fsdecode(b"\xff" * 240), CLAUSE_DROP_INFIX, "f" * 200, 0, ".c"
    )
    assert len(os.fsencode(long_name)) <= 255
    assert "__clause_drop_" in long_name


def test_bounded_mutant_file_name_does_not_split_multibyte_characters() -> None:
    name = get_bounded_mutant_file_name("é" * 120, CLAUSE_DROP_INFIX, "f" * 200, 0, ".c")
    encoded = os.fsencode(name)
    assert len(encoded) <= 255
    encoded.decode("utf-8")  # Raises if a multibyte character was split.
