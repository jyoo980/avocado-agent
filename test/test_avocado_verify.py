"""Tests for `avocado_verify`."""

from avocado_verify import _build_prompt  # ruff: ignore[import-private-name]


def test_build_prompt_without_include_dirs() -> None:
    """Test that the prompt omits `-I` flags when there are no include directories."""
    assert _build_prompt("f", file_path="/p/src/f.c", include_dirs=[]) == "Verify f in /p/src/f.c"


def test_build_prompt_with_include_dirs() -> None:
    """Test that the prompt lists the include directories, in order and shell-quoted."""
    prompt = _build_prompt("f", file_path="/p/src/f.c", include_dirs=["/stubs", "/p/my include"])
    assert prompt == (
        "Verify f in /p/src/f.c. Whenever you run avocado-run-cbmc, pass "
        "`-I /stubs -I '/p/my include'`, in that order."
    )
