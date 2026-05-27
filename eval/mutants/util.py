"""Utilities for mutation testing."""

from pathlib import Path

from tools.run_cbmc import CbmcStep, RunCbmcResult

_CBMC_RETURN_CODES_FOR_SUCCESS_AND_FAILURE = frozenset({0, 10})


def is_valid_mutation_candidate(run_cbmc_result: RunCbmcResult) -> bool:
    """Return True iff the function in the given CBMC run is a valid mutation candidate.

    A valid mutation candidate must be a function that already successfully verifies.

    Args:
        run_cbmc_result (RunCbmcResult): The CBMC run result for the function to (possibly) mutate.

    Returns:
        bool: True iff the function in the given CBMC run is a valid mutation candidate.
    """
    if failed_step := run_cbmc_result.failed_step:
        return failed_step != CbmcStep.CBMC
    return not run_cbmc_result.timed_out


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
        msg = f"{path} does not exist; double-check for full or relative paths"
        raise RuntimeError(msg)
    if path.is_dir():
        return sorted(path.rglob(f"*{extension}"))
    return [path] if path.suffix == extension else []
