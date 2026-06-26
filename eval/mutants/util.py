"""Utilities for mutation testing."""

from pathlib import Path

# 0 indicates success, 10 indicates failure, 124 indicates timeout.
_CBMC_RETURN_CODES_SUCCESS_FAILURE_TIMEOUT = frozenset({0, 10, 124})


def check_expected_cbmc_return_code(return_code: int):
    """Check whether the return code is indicates a verification success, failure, or timeout.

    Args:
        return_code (int): The return code to check for verification success, failure, or timeout.
    """
    if return_code not in _CBMC_RETURN_CODES_SUCCESS_FAILURE_TIMEOUT:
        msg = (
            f"Unexpected CBMC return code: {return_code}. "
            "See: https://diffblue.github.io/cbmc/exit__codes_8h.html"
        )
        raise RuntimeError(msg)


def get_files_with_extension(path_str: str, extension: str) -> list[Path]:
    """Return the path(s) to the file(s) with the given extension at the path.

    If the path resolves to a directory, recursively collect files.

    Args:
        path_str (str): Path to a file or directory.
        extension (str): The extension to match files on.

    Returns:
        list[Path]: The path(s) to the file(s) with the given extension at the path.
    """
    path = Path(path_str)
    if not path.exists():
        msg = f"{path} does not exist; double-check for full or relative paths"
        raise RuntimeError(msg)
    if path.is_dir():
        return sorted(path.rglob(f"*{extension}"))
    return [path] if path.suffix == extension else []
