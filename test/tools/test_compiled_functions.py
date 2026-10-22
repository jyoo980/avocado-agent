"""Tests for functions that are in the source text but not in the compiled program."""

from pathlib import Path

import avocado_verify
from tools.run_cbmc import DEFAULT_CBMC_DEPTH, list_compiled_functions
from tools.run_cbmc_and_mutation_testing import verify_function
from tools.util import get_call_graph

SOURCE = b"""static int twice(int x)
__CPROVER_requires(0 <= x && x < 8)
__CPROVER_ensures(__CPROVER_return_value == 2 * x)
{ return x + x; }

#ifdef AVOCADO_TEST_NEVER_DEFINED
static int thrice(int x)
__CPROVER_requires(0 <= x && x < 8)
__CPROVER_ensures(__CPROVER_return_value == 3 * x)
{ return x + x + x; }
#endif

int scale(int x)
__CPROVER_requires(0 <= x && x < 8)
__CPROVER_ensures(__CPROVER_return_value == 2 * x)
{
#ifdef AVOCADO_TEST_NEVER_DEFINED
    return thrice(x);
#else
    return twice(x);
#endif
}
"""


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "build.c"
    source.write_bytes(SOURCE)
    return source


def test_the_compiler_says_which_functions_exist(tmp_path: Path) -> None:
    source = _source(tmp_path)
    # Read as text, the file defines three functions, and `scale` calls both of the others.
    call_graph = get_call_graph(str(source))
    assert set(call_graph) == {"twice", "thrice", "scale"}
    assert call_graph.get_callees("scale").internal == ["thrice", "twice"]
    assert list_compiled_functions(str(source)) == {"twice", "scale"}
    # Nothing is left behind beside the source.
    assert [path.name for path in tmp_path.iterdir()] == ["build.c"]


def test_a_file_that_does_not_compile_tells_nothing(tmp_path: Path) -> None:
    broken = tmp_path / "broken.c"
    broken.write_text("int f(void) { return }\n")
    assert list_compiled_functions(str(broken)) is None
    assert list_compiled_functions(str(tmp_path / "missing.c")) is None


def test_a_function_is_verified_without_the_callees_the_preprocessor_removes(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    # `thrice` is in the callee list read from the text but not in the program; the pipeline
    # leaves it out instead of failing at goto-instrument.
    result = verify_function("scale", str(source), depth=DEFAULT_CBMC_DEPTH)
    assert result.is_function_verified, result.response
    # A function that is not compiled still cannot be verified itself.
    assert str(verify_function("thrice", str(source), depth=DEFAULT_CBMC_DEPTH)) == "GOTO-CC_FAILED"


def test_the_harness_gives_functions_outside_the_build_no_session(
    tmp_path: Path, monkeypatch
) -> None:
    source = _source(tmp_path)
    order = ["twice", "thrice", "scale"]
    assert avocado_verify._functions_not_in_build(order, str(source), []) == ["thrice"]
    # When the file cannot be compiled nothing is known, so nothing is left out.
    monkeypatch.setattr(avocado_verify, "list_compiled_functions", lambda *a, **k: None)
    assert avocado_verify._functions_not_in_build(order, str(source), []) == []


def test_functions_outside_the_build_are_no_ones_callee_and_are_recorded_unverified(
    tmp_path: Path,
) -> None:
    call_graph = avocado_verify._without_functions(get_call_graph(str(_source(tmp_path))), ["thrice"])
    assert set(call_graph) == {"twice", "scale"}
    assert call_graph.get_callees("scale").internal == ["twice"]
    # `scale` no longer waits for, or is told about, a callee that does not exist.
    ready = avocado_verify._ready_functions(
        ["twice", "thrice", "scale"], call_graph, done={"twice", "thrice"}, active=set()
    )
    assert ready == ["scale"]
    record = avocado_verify._not_in_build_result("thrice").to_record()
    assert record["outcome"] == "UNVERIFIED" and record["not_in_build"] is True
    assert record["cbmc"]["verdict"] == "GOTO-CC_FAILED"
    assert record["agent_sessions"] == 0 and record["total_cost_to_verify_usd"] == 0
    # It counts towards the total, as an unverified function, and the summary names it.
    summary = avocado_verify._run_summary_record(
        "completed",
        done=["twice", "thrice", "scale"],
        remaining=[],
        verified=2,
        total=3,
        not_in_build=["thrice"],
    )
    assert (summary["verified"], summary["total"]) == (2, 3)
    assert summary["not_in_build"] == ["thrice"]
