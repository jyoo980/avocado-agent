from __future__ import annotations

from loguru import logger

from proof_state.next_step import AgentDecision
from proof_state.proof_state import ProofState
from util.c_function_graph import CFunctionGraph
from util.function_specification import FunctionSpecification

MAX_BACKTRACKS_PER_FUNCTION = 3


class ProofStateStepper:
    def __init__(self, function_graph: CFunctionGraph) -> None:
        self._graph = function_graph

    def transition(self, state: ProofState, decision: AgentDecision) -> ProofState:
        """Compute the next ProofState given an AgentDecision on state.peek_workstack()."""
        work_item = state.peek_workstack()
        fn = work_item.function
        spec = decision.spec or FunctionSpecification()

        if decision.type == "ACCEPT":
            logger.success(f"[{fn.name}] ACCEPTED — spec verified by CBMC")
            return state.accept(fn, spec)

        if decision.type == "ASSUME":
            logger.warning(f"[{fn.name}] ASSUMED — could not verify spec, accepting without proof")
            return state.assume(fn, spec)

        if decision.type == "BACKTRACK":
            return self._handle_backtrack(state, fn, spec, decision)

        raise ValueError(f"Unknown decision type: {decision.type!r}")

    def _handle_backtrack(
        self,
        state: ProofState,
        fn: "CFunction",
        spec: FunctionSpecification,
        decision: AgentDecision,
    ) -> ProofState:
        callee_name = decision.backtrack_callee
        hint = decision.backtrack_hint or ""

        if not callee_name:
            logger.warning(f"[{fn.name}] BACKTRACK requested but no callee named — forcing ASSUME")
            return state.assume(fn, spec)

        callee = self._graph.get_function_or_none(callee_name)
        if callee is None:
            logger.warning(
                f"[{fn.name}] BACKTRACK to '{callee_name}' — not in graph (external?), forcing ASSUME"
            )
            return state.assume(fn, spec)

        if state.workstack_contains(callee, hint):
            logger.warning(
                f"[{fn.name}] BACKTRACK to '{callee_name}' would create a cycle — forcing ASSUME"
            )
            return state.assume(fn, spec)

        if state.backtrack_count(callee) >= MAX_BACKTRACKS_PER_FUNCTION:
            logger.warning(
                f"[{fn.name}] BACKTRACK to '{callee_name}' exceeded max backtrack budget — forcing ASSUME"
            )
            return state.assume(fn, spec)

        logger.info(f"[{fn.name}] BACKTRACK → re-queuing '{callee_name}' with hint: {hint!r}")
        return state.push_callee(callee, hint)
