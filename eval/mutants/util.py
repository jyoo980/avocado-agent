"""Utilities for mutation testing."""

from pathlib import Path

_CBMC_RETURN_CODES_FOR_SUCCESS_AND_FAILURE = frozenset({0, 10})


def check_expected_cbmc_return_code(return_code: int):
    """Check whether the return code is indicates either a verification success or failure.

    Args:
        return_code (int): The return code to check for verification success or failure.
    """
    if return_code not in _CBMC_RETURN_CODES_FOR_SUCCESS_AND_FAILURE:
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
        return []
    if path.is_dir():
        return sorted(path.rglob(f"*{extension}"))
    return [path] if path.suffix == extension else []
