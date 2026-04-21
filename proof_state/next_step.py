from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from util.function_specification import FunctionSpecification


@dataclass
class AgentDecision:
    type: Literal["ACCEPT", "ASSUME", "BACKTRACK"]
    spec: FunctionSpecification | None = None
    backtrack_callee: str | None = None
    backtrack_hint: str | None = None
