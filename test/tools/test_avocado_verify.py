"""Tests for the per-function prompt built by `avocado_verify`."""

from avocado_verify import _build_claude_command, _build_prompt
from tools.util.callgraph import CallGraph


def _quicksort_call_graph() -> CallGraph:
    return CallGraph(
        {
            "swap": {"internal": [], "external": []},
            "partition": {"internal": ["swap"], "external": []},
            "quickSort": {"internal": ["partition", "quickSort"], "external": []},
        }
    )


def test_prompt_names_the_tool_command_callees_and_callers() -> None:
    prompt = _build_prompt(
        "partition",
        file_path="/src/quicksort.c",
        call_graph=_quicksort_call_graph(),
        include_dirs=["/src/include"],
    )
    assert prompt.startswith("Verify partition in /src/quicksort.c.")
    assert "avocado-run-cbmc --function partition --file /src/quicksort.c -I /src/include" in prompt
    assert "callees" in prompt and ": swap" in prompt
    assert "callers" in prompt and ": quickSort" in prompt


def test_prompt_reports_none_for_leaf_functions_without_include_dirs() -> None:
    prompt = _build_prompt(
        "swap", file_path="/src/quicksort.c", call_graph=_quicksort_call_graph(), include_dirs=[]
    )
    assert "avocado-run-cbmc --function swap --file /src/quicksort.c\n" in prompt
    assert "-I" not in prompt
    assert "function is verified): none" in prompt
    assert "you write): partition" in prompt


def test_claude_command_leaves_auto_memory_enabled() -> None:
    # Memory across functions and runs is a deliberate feature of the harness; no `--settings`
    # override may switch it off.
    command = _build_claude_command("prompt", file_path="/src/q.c", include_dirs=["/src/inc"])
    assert "--settings" not in command
    assert command[:3] == ["claude", "--print", "prompt"]
    assert command.count("--add-dir") == 2
