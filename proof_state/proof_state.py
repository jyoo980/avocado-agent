from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from util.c_function import CFunction
    from util.function_specification import FunctionSpecification


class FunctionStatus(Enum):
    UNPROCESSED = "unprocessed"
    SUCCEEDED = "succeeded"
    ASSUMED = "assumed"


@dataclass(frozen=True)
class WorkItem:
    function: "CFunction"
    hint: str = ""
    assume_without_verification: bool = False


class ProofState:
    """Immutable snapshot of the verification progress.

    Immutability is achieved by never mutating in-place: all transition methods
    return a new ProofState. The state can be hashed (via id of the internal
    tuple representation) for `seen`-set cycle detection.
    """

    def __init__(
        self,
        functions: list["CFunction"],
        workstack: tuple[WorkItem, ...] | None = None,
        statuses: dict["CFunction", FunctionStatus] | None = None,
        specs: dict["CFunction", "FunctionSpecification"] | None = None,
        backtrack_counts: dict["CFunction", int] | None = None,
    ) -> None:
        if workstack is None:
            workstack = tuple(WorkItem(fn) for fn in functions)
        self._workstack: tuple[WorkItem, ...] = workstack
        self._statuses: dict["CFunction", FunctionStatus] = dict(
            statuses or {fn: FunctionStatus.UNPROCESSED for fn in functions}
        )
        self._specs: dict["CFunction", "FunctionSpecification"] = dict(specs or {})
        self._backtrack_counts: dict["CFunction", int] = dict(backtrack_counts or {})

    # ── Read accessors ────────────────────────────────────────────────────────

    def peek_workstack(self) -> WorkItem:
        if not self._workstack:
            raise IndexError("Workstack is empty")
        return self._workstack[0]

    def is_workstack_empty(self) -> bool:
        return len(self._workstack) == 0

    def get_status(self, fn: "CFunction") -> FunctionStatus:
        return self._statuses.get(fn, FunctionStatus.UNPROCESSED)

    def get_specification(self, fn: "CFunction") -> "FunctionSpecification | None":
        return self._specs.get(fn)

    def backtrack_count(self, fn: "CFunction") -> int:
        return self._backtrack_counts.get(fn, 0)

    def workstack_contains(self, fn: "CFunction", hint: str) -> bool:
        return any(item.function == fn and item.hint == hint for item in self._workstack)

    def all_statuses(self) -> dict["CFunction", FunctionStatus]:
        return dict(self._statuses)

    def all_specs(self) -> dict["CFunction", "FunctionSpecification"]:
        return dict(self._specs)

    # ── Transition methods (return new ProofState) ────────────────────────────

    def accept(self, fn: "CFunction", spec: "FunctionSpecification") -> "ProofState":
        """Pop fn from workstack, mark as SUCCEEDED, record spec."""
        return self._pop_and_record(fn, FunctionStatus.SUCCEEDED, spec)

    def assume(self, fn: "CFunction", spec: "FunctionSpecification | None") -> "ProofState":
        """Pop fn from workstack, mark as ASSUMED."""
        return self._pop_and_record(fn, FunctionStatus.ASSUMED, spec)

    def push_callee(self, callee: "CFunction", hint: str) -> "ProofState":
        """Push callee onto the front of the workstack for re-processing."""
        new_counts = dict(self._backtrack_counts)
        new_counts[callee] = new_counts.get(callee, 0) + 1
        new_item = WorkItem(callee, hint=hint)
        new_workstack = (new_item,) + self._workstack
        return ProofState(
            functions=[],
            workstack=new_workstack,
            statuses=self._statuses,
            specs=self._specs,
            backtrack_counts=new_counts,
        )

    def _pop_and_record(
        self,
        fn: "CFunction",
        status: FunctionStatus,
        spec: "FunctionSpecification | None",
    ) -> "ProofState":
        new_workstack = tuple(item for item in self._workstack if item.function != fn)
        new_statuses = {**self._statuses, fn: status}
        new_specs = dict(self._specs)
        if spec is not None:
            new_specs[fn] = spec
        return ProofState(
            functions=[],
            workstack=new_workstack,
            statuses=new_statuses,
            specs=new_specs,
            backtrack_counts=self._backtrack_counts,
        )

    # ── Hashing / equality (for `seen` set) ───────────────────────────────────

    def _state_key(self) -> tuple:
        workstack_key = tuple((item.function.name, item.hint) for item in self._workstack)
        status_key = tuple(sorted((fn.name, s.value) for fn, s in self._statuses.items()))
        return (workstack_key, status_key)

    def __hash__(self) -> int:
        return hash(self._state_key())

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ProofState):
            return NotImplemented
        return self._state_key() == other._state_key()

    def summary(self) -> str:
        total = len(self._statuses)
        succeeded = sum(1 for s in self._statuses.values() if s == FunctionStatus.SUCCEEDED)
        assumed = sum(1 for s in self._statuses.values() if s == FunctionStatus.ASSUMED)
        pending = len(self._workstack)
        return (
            f"ProofState: {total} functions | "
            f"{succeeded} succeeded | {assumed} assumed | {pending} pending"
        )
