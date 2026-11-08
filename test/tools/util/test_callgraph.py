"""Tests for CallGraph and CallGraphCallees JSON serialization."""

import json

from tools.util.callgraph import CallGraph, CallGraphCallees


def test_callgraph_is_json_serializable() -> None:
    call_graph = CallGraph({
        "foo": {"internal": ["bar"], "external": ["printf"]},
        "bar": {"internal": [], "external": ["malloc", "strcpy"]},
    })

    decoded = json.loads(json.dumps(call_graph))

    assert decoded == {
        "foo": {"internal": ["bar"], "external": ["printf"]},
        "bar": {"internal": [], "external": ["malloc", "strcpy"]},
    }


def test_callgraph_round_trip() -> None:
    original = CallGraph({
        "foo": {"internal": ["bar"], "external": ["printf"]},
        "bar": {"internal": [], "external": ["malloc"]},
    })

    restored = CallGraph(json.loads(json.dumps(original)))

    assert restored.size() == original.size()
    assert restored.get_callees("foo").internal == ["bar"]
    assert restored.get_callees("foo").external == ["printf"]
    assert restored.get_callees("bar").internal == []
    assert restored.get_callees("bar").external == ["malloc"]


def test_callgraph_callees_is_json_serializable() -> None:
    callees = CallGraphCallees(internal=["bar"], external=["printf", "malloc"])

    decoded = json.loads(json.dumps(callees))

    assert decoded == {"internal": ["bar"], "external": ["printf", "malloc"]}
