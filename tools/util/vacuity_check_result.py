"""Represents the result of running a vacuity check on a function via CBMC."""

from dataclasses import dataclass
from enum import StrEnum


class Vacuity(StrEnum):
    """Whether a passing verification says anything about the function.

    A function verifies *vacuously* when no execution of the function CBMC explores reaches the end
    of the function. This means the postconditions of the function are never checked.

    `tools.util.check_vacuity` decides this with a run of the CBMC pipeline for a function.
    """

    # At least one execution path enters the body and reaches the end of the function.
    NOT_VACUOUS = "not_vacuous"
    # No execution enters the body: the preconditions cannot all hold at once.
    UNSATISFIABLE_PRECONDITIONS = "unsatisfiable_preconditions"
    # Executions enter the body, but none reach the end of the function.
    END_UNREACHABLE = "end_unreachable"
    # The CBMC run did not give an answer (it timed out or failed). The prior result is maintained,
    # i.e., it may or may not be a vacuous verification result.
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class VacuityCheckResult:
    """Represents the result of running a vacuity check on a function via CBMC.

    Attributes:
        vacuity (Vacuity): The status of a vacuity check on a function.
        message (str): Additional information for the status of the vacuity check.
    """

    vacuity: Vacuity
    message: str
