from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from proof_state.next_step import AgentDecision
from proof_state.proof_state import FunctionStatus, ProofState, WorkItem
from proof_state.proof_state_stepper import MAX_BACKTRACKS_PER_FUNCTION, ProofStateStepper
from util.c_function import CFunction
from util.function_specification import FunctionSpecification


def _make_fn(name: str) -> CFunction:
    return CFunction(name=name, file_path=Path(f"{name}.c"), start_line=1, end_line=10)


def _make_graph(functions: list[CFunction], edges: list[tuple] | None = None):
    graph = MagicMock()
    fn_by_name = {fn.name: fn for fn in functions}
    graph.get_function_or_none.side_effect = lambda name: fn_by_name.get(name)

    callee_map: dict[str, list[CFunction]] = {fn.name: [] for fn in functions}
    if edges:
        for caller, callee in edges:
            callee_map[caller.name].append(callee)
    graph.get_callees.side_effect = lambda fn: callee_map.get(fn.name, [])
    return graph


def test_accept_transition():
    fn = _make_fn("foo")
    state = ProofState([fn])
    graph = _make_graph([fn])
    stepper = ProofStateStepper(graph)
    spec = FunctionSpecification(postconditions=["__CPROVER_return_value > 0"])

    decision = AgentDecision(type="ACCEPT", spec=spec)
    next_state = stepper.transition(state, decision)

    assert next_state.get_status(fn) == FunctionStatus.SUCCEEDED
    assert next_state.is_workstack_empty()
    assert next_state.get_specification(fn) == spec


def test_assume_transition():
    fn = _make_fn("bar")
    state = ProofState([fn])
    graph = _make_graph([fn])
    stepper = ProofStateStepper(graph)

    decision = AgentDecision(type="ASSUME", spec=FunctionSpecification())
    next_state = stepper.transition(state, decision)

    assert next_state.get_status(fn) == FunctionStatus.ASSUMED
    assert next_state.is_workstack_empty()


def test_backtrack_pushes_callee():
    caller = _make_fn("caller")
    callee = _make_fn("callee")
    state = ProofState([callee, caller])
    # Pop callee off (it's already been processed first in topo order)
    state = state.accept(callee, FunctionSpecification())
    graph = _make_graph([caller, callee], edges=[(caller, callee)])
    stepper = ProofStateStepper(graph)

    decision = AgentDecision(
        type="BACKTRACK",
        spec=FunctionSpecification(),
        backtrack_callee="callee",
        backtrack_hint="ensure return_value >= 0",
    )
    next_state = stepper.transition(state, decision)

    # callee should be back on the workstack
    assert not next_state.is_workstack_empty()
    item = next_state.peek_workstack()
    assert item.function == callee
    assert item.hint == "ensure return_value >= 0"


def test_backtrack_cycle_detection():
    caller = _make_fn("caller")
    callee = _make_fn("callee")
    hint = "ensure return_value >= 0"
    graph = _make_graph([caller, callee], edges=[(caller, callee)])
    stepper = ProofStateStepper(graph)

    # State: caller is on top of workstack; callee_with_hint is already below.
    # Build this by constructing workstack items directly.
    callee_item = WorkItem(callee, hint=hint)
    caller_item = WorkItem(caller)
    state = ProofState(
        functions=[],
        workstack=(caller_item, callee_item),
        statuses={caller: FunctionStatus.UNPROCESSED, callee: FunctionStatus.UNPROCESSED},
    )

    decision = AgentDecision(
        type="BACKTRACK",
        spec=FunctionSpecification(),
        backtrack_callee="callee",
        backtrack_hint=hint,
    )
    next_state = stepper.transition(state, decision)
    # Should force ASSUME due to cycle detection — caller gets assumed
    assert next_state.get_status(caller) == FunctionStatus.ASSUMED


def test_backtrack_max_budget():
    caller = _make_fn("caller")
    callee = _make_fn("callee")
    graph = _make_graph([caller, callee], edges=[(caller, callee)])
    stepper = ProofStateStepper(graph)

    # Build a state with exhausted backtrack budget for callee AND caller on workstack.
    # Start with caller on the workstack and callee's backtrack count at the max.
    caller_item = WorkItem(caller)
    state = ProofState(
        functions=[],
        workstack=(caller_item,),
        statuses={caller: FunctionStatus.UNPROCESSED, callee: FunctionStatus.ASSUMED},
        backtrack_counts={callee: MAX_BACKTRACKS_PER_FUNCTION},
    )

    decision = AgentDecision(
        type="BACKTRACK",
        spec=FunctionSpecification(),
        backtrack_callee="callee",
        backtrack_hint="yet another hint",
    )
    next_state = stepper.transition(state, decision)
    assert next_state.get_status(caller) == FunctionStatus.ASSUMED


def test_backtrack_unknown_callee_forces_assume():
    fn = _make_fn("foo")
    state = ProofState([fn])
    graph = _make_graph([fn])
    stepper = ProofStateStepper(graph)

    decision = AgentDecision(
        type="BACKTRACK",
        spec=FunctionSpecification(),
        backtrack_callee="nonexistent_external_func",
        backtrack_hint="make it return 0",
    )
    next_state = stepper.transition(state, decision)
    assert next_state.get_status(fn) == FunctionStatus.ASSUMED


def test_proof_state_hashing():
    fn = _make_fn("foo")
    s1 = ProofState([fn])
    s2 = ProofState([fn])
    assert s1 == s2
    assert hash(s1) == hash(s2)

    s3 = s1.accept(fn, FunctionSpecification())
    assert s3 != s1
    seen = {s1}
    assert s1 in seen
    assert s3 not in seen
