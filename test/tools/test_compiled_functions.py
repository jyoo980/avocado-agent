"""Tests for functions that are in the source text but not in the compiled program."""

from pathlib import Path

from tools.run_cbmc import list_compiled_functions
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
    result = verify_function("scale", str(source))
    assert result.is_function_verified, result.response
    # A function that is not compiled still cannot be verified itself.
    assert str(verify_function("thrice", str(source))) == "GOTO-CC_FAILED"

