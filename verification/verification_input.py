from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from util.c_function import CFunction
from util.function_specification import FunctionSpecification


@dataclass
class VerificationInput:
    function: CFunction
    spec: FunctionSpecification
    callee_specs: dict[str, FunctionSpecification] = field(default_factory=dict)
    unwind: int = 5

    def cache_key(self) -> str:
        """Stable hash over all fields that affect verification outcome."""
        payload = {
            "function": self.function.name,
            "source": self.function.get_source_code(),
            "spec_preconditions": self.spec.preconditions,
            "spec_postconditions": self.spec.postconditions,
            "spec_assigns": self.spec.assigns,
            "spec_loops": [
                {
                    "loop_id": lc.loop_id,
                    "invariant": lc.invariant,
                    "decreases": lc.decreases,
                    "assigns": lc.assigns,
                }
                for lc in self.spec.loop_contracts
            ],
            "callee_specs": {
                name: {
                    "preconditions": s.preconditions,
                    "postconditions": s.postconditions,
                    "assigns": s.assigns,
                }
                for name, s in sorted(self.callee_specs.items())
            },
            "unwind": self.unwind,
        }
        serialized = json.dumps(payload, sort_keys=True)
        return hashlib.sha256(serialized.encode()).hexdigest()
