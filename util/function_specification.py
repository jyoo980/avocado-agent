from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class LoopContract:
    loop_id: int
    invariant: str
    decreases: str | None = None
    assigns: list[str] = field(default_factory=list)


@dataclass
class FunctionSpecification:
    preconditions: list[str] = field(default_factory=list)
    postconditions: list[str] = field(default_factory=list)
    assigns: list[str] = field(default_factory=list)
    loop_contracts: list[LoopContract] = field(default_factory=list)

    def has_loop_contracts(self) -> bool:
        return len(self.loop_contracts) > 0

    def is_empty(self) -> bool:
        return (
            not self.preconditions
            and not self.postconditions
            and not self.assigns
            and not self.loop_contracts
        )

    def to_cbmc_annotation(self) -> str:
        """Render the spec as C comment + contract annotations for insertion above a function."""
        lines: list[str] = []
        for pre in self.preconditions:
            lines.append(f"__CPROVER_requires({pre})")
        for post in self.postconditions:
            lines.append(f"__CPROVER_ensures({post})")
        for target in self.assigns:
            lines.append(f"__CPROVER_assigns({target})")
        return "\n".join(lines)

    def to_display_string(self) -> str:
        parts: list[str] = []
        if self.preconditions:
            parts.append("Preconditions:")
            parts.extend(f"  __CPROVER_requires({p})" for p in self.preconditions)
        if self.postconditions:
            parts.append("Postconditions:")
            parts.extend(f"  __CPROVER_ensures({p})" for p in self.postconditions)
        if self.assigns:
            parts.append("Assigns:")
            parts.extend(f"  __CPROVER_assigns({a})" for a in self.assigns)
        if self.loop_contracts:
            parts.append("Loop contracts:")
            for lc in self.loop_contracts:
                parts.append(f"  Loop {lc.loop_id}:")
                parts.append(f"    invariant: {lc.invariant}")
                if lc.decreases:
                    parts.append(f"    decreases: {lc.decreases}")
                if lc.assigns:
                    parts.extend(f"    assigns: {a}" for a in lc.assigns)
        return "\n".join(parts) if parts else "(empty spec)"
