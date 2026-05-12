"""Utilities for mutation testing."""

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
