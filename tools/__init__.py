"""CLI tools for specification generation and verification."""

from .run_cbmc import get_cbmc_command, missing_body_for_callee

__all__ = ["get_cbmc_command", "missing_body_for_callee"]
