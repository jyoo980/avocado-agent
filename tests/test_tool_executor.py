"""Tests for the MCP server tool handlers (replaces test_tool_executor.py)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import agent.mcp_server as server
from util.c_function import CFunction
from util.function_specification import FunctionSpecification
from verification.verification_result import VerificationResult, VerificationStatus


def _make_fn(name: str, source: str = "int foo(int x) { return x + 1; }") -> CFunction:
    fn = MagicMock(spec=CFunction)
    fn.name = name
    fn.get_source_code.return_value = source
    return fn


@pytest.fixture(autouse=True)
def reset_server_state():
    """Reset module-level mutable state before each test."""
    fn = _make_fn("foo")
    server._current_fn = fn
    server._callees = []
    server._callers = []
    server._all_functions = {"foo": fn}
    server._specs = {}
    server._pending_spec = None
    server._last_cbmc_result = None

    mock_cbmc = MagicMock()
    mock_cbmc.verify.return_value = VerificationResult(
        status=VerificationStatus.SUCCEEDED,
        raw_output="<cprover><cprover-status>SUCCESS</cprover-status></cprover>",
    )
    server._cbmc_client = mock_cbmc
    yield


def test_read_function():
    result = server.read_function("foo")
    assert "foo" in result
    assert "return x + 1" in result


def test_read_function_unknown():
    result = server.read_function("nonexistent")
    assert "not found" in result.lower()


def test_write_spec_stores_spec():
    server.write_spec(
        function_name="foo",
        preconditions=["x > 0"],
        postconditions=["__CPROVER_return_value > 0"],
        assigns=[],
    )
    assert server._pending_spec is not None
    assert server._pending_spec.preconditions == ["x > 0"]
    assert server._pending_spec.postconditions == ["__CPROVER_return_value > 0"]


def test_write_spec_wrong_function():
    result = server.write_spec(
        function_name="bar",
        preconditions=[],
        postconditions=[],
        assigns=[],
    )
    assert "error" in result.lower()


def test_run_cbmc_requires_spec():
    result = server.run_cbmc("foo")
    assert "no spec" in result.lower() or "write_spec" in result.lower()


def test_run_cbmc_succeeds():
    server._pending_spec = FunctionSpecification(postconditions=["__CPROVER_return_value >= 0"])
    result = server.run_cbmc("foo")
    assert "SUCCEEDED" in result


def test_run_cbmc_stores_result():
    server._pending_spec = FunctionSpecification()
    server.run_cbmc("foo")
    assert server._last_cbmc_result is not None


def test_finish_accept_writes_result(tmp_path):
    result_file = tmp_path / "result.json"
    server._result_file = result_file
    server._pending_spec = FunctionSpecification()

    msg = server.finish(decision="ACCEPT")
    assert "ACCEPT" in msg
    assert result_file.exists()

    import json

    data = json.loads(result_file.read_text())
    assert data["decision"] == "ACCEPT"


def test_finish_backtrack_writes_result(tmp_path):
    result_file = tmp_path / "result.json"
    server._result_file = result_file
    server._pending_spec = FunctionSpecification()

    server.finish(
        decision="BACKTRACK",
        backtrack_callee="bar",
        backtrack_hint="ensure return_value >= 0",
    )

    import json

    data = json.loads(result_file.read_text())
    assert data["decision"] == "BACKTRACK"
    assert data["backtrack_callee"] == "bar"
    assert data["backtrack_hint"] == "ensure return_value >= 0"


def test_get_callees_empty():
    result = server.get_callees("foo")
    assert "no callees" in result.lower() or "leaf" in result.lower()


def test_get_counterexample_no_run():
    result = server.get_counterexample("foo")
    assert "no cbmc run" in result.lower() or "no" in result.lower()
