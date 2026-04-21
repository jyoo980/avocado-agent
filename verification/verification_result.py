from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class VerificationStatus(Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMEOUT = "timeout"
    PARSE_ERROR = "parse_error"
    COMPILE_ERROR = "compile_error"


@dataclass
class CbmcTraceStep:
    step_nr: int
    function: str
    location: str
    assignments: dict[str, str] = field(default_factory=dict)


@dataclass
class CbmcCounterexample:
    failing_property: str
    failure_location: str
    trace: list[CbmcTraceStep] = field(default_factory=list)

    def to_display_string(self) -> str:
        lines = [
            f"Failing property: {self.failing_property}",
            f"Location: {self.failure_location}",
            "Trace:",
        ]
        for step in self.trace:
            assignments = ", ".join(f"{k}={v}" for k, v in step.assignments.items())
            lines.append(
                f"  Step {step.step_nr} [{step.function} @ {step.location}]: {assignments}"
            )
        return "\n".join(lines)


@dataclass
class VerificationResult:
    status: VerificationStatus
    failure_descriptions: list[str] = field(default_factory=list)
    counterexample: CbmcCounterexample | None = None
    raw_output: str = ""

    @property
    def succeeded(self) -> bool:
        return self.status == VerificationStatus.SUCCEEDED
