"""Helpers for determining which CBMC stub files are needed to verify a function."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tools.util.callgraph import CallGraph

_STUBS_DIR = Path(__file__).resolve().parents[2] / "stubs"

# CBMC stub files mark each modeled symbol with a `/* FUNCTION: <name> */` comment immediately
# preceding the C function definition. In the C function definition, the C identifier itself is
# often a renamed/prefixed alias (e.g.  `_avocado_printf`), so the comment marker is the source of
# truth for the symbol name.
_FUNCTION_MARKER = re.compile(r"/\*\s*FUNCTION:\s*(\S+)\s*\*/")


def build_stub_index(stubs_dir: Path = _STUBS_DIR) -> dict[str, Path]:
    """Return a mapping from modeled function name to the stub file defining it.

    Symbol names are read from `/* FUNCTION: <name> */` markers. If a name appears in more than
    one stub file, the first one encountered (in sorted-path order) wins.

    Args:
        stubs_dir (Path): The directory containing CBMC stub `.c` files.

    Returns:
        dict[str, Path]: Mapping from function name to the stub file that models it (that is, the
            stub file that defines its stub).
    """
    index: dict[str, Path] = {}
    paths = sorted(stubs_dir.glob("*.c"))
    for stub_path in paths:
        for name in _FUNCTION_MARKER.findall(stub_path.read_text(encoding="utf-8")):
            index.setdefault(name, stub_path)
            # [[MDE: I think it be an error if a function name appears more than once.]]
    return index


def get_stub_paths_for(
    function_to_verify: str,
    call_graph: CallGraph,
    stub_index: dict[str, Path],
) -> list[str]:
    """Return the stub file paths needed to verify `function_to_verify`.

    External callees come from the call graph's `external` list for `function_to_verify`. Each
    external callee is looked up in the stub index; unresolved names are dropped. The CBMC-bundled
    library models must be injected by `goto-instrument --add-library` instead).

    Args:
        function_to_verify (str): The function whose callees should be resolved.
        call_graph (CallGraph): The call graph.
        stub_index (dict[str, Path]): Stub index produced by `build_stub_index` (or
            `build_extra_stub_index` when only compile-safe stubs are wanted).

    Returns:
        list[str]: Sorted, de-duplicated list of stub file paths.
    """
    external_callees = call_graph.get_callees(function_to_verify).external
    resolved = {stub_index[name] for name in external_callees if name in stub_index}
    return sorted(str(path) for path in resolved)


def get_in_file_callees_for(
    function_to_verify: str,
    call_graph: CallGraph,
    include_self: bool = False,
) -> list[str]:
    """Return direct callees of `function_to_verify` that are defined in the same C file.

    These are the candidates to pass to CBMC via `--replace-call-with-contract`. By default the
    function itself is excluded; set `include_self=True` to keep it so a self-recursive call gets
    rewritten into a contract call. That is the standard pattern for inductively verifying a
    recursive function: pass it to both `--enforce-contract` and `--replace-call-with-contract`
    so the recursive call is discharged by the function's own contract instead of being unwound.
    The contract must be inductive — strong enough to imply itself at the recursive call site —
    or the proof is vacuous.

    [[MDE: I found the above comment very helpful in understanding your concern with recursive
    calls.  Thanks.  It is always sound to use `--replace-call-with-contract` for a recursive call,
    but it might not work if the contract is not inductive.  If the contract is not inductive, I'm
    not convinced that it will work with inlining 5 times, but maybe it will.  Given your
    explanation, I think that the scripts should try both approaches rather than asking the user
    (who might be a human when debugging, a test suite, or the LLM) to figure this out.  And given
    that suggestion, this function `get_in_file_callees_for()` should probably not exclude the
    function itself.]]

    Args:
        function_to_verify (str): The function whose in-file callees should be returned.
        call_graph (CallGraph): The call graph.
        include_self (bool): When True, keep `function_to_verify` in the result if it calls
            itself. Defaults to False.

    Returns:
        list[str]: Sorted, de-duplicated list of in-file callee names.

    """
    internal_callees = call_graph.get_callees(function_to_verify).internal
    if include_self:
        return sorted(set(internal_callees))
    return sorted({name for name in internal_callees if name != function_to_verify})


def get_in_file_callers_of(
    function: str,
    call_graph: CallGraph,
) -> list[str]:
    """Return functions in the call graph that directly call `function` in the same file.

    Symmetric to `get_in_file_callees_for`: a caller is "in-file" iff it appears as a key of the
    call graph (the call graph is constructed per-file) and its `internal` callee list includes
    `function`. The function itself is excluded so a self-recursive call is not reported as a
    caller of itself.

    Args:
        function (str): The function whose in-file callers should be returned.
        call_graph (CallGraph): The call graph.

    Returns:
        list[str]: Sorted, de-duplicated list of in-file caller names.
    """
    return sorted(
        {
            caller
            for caller, callees in call_graph.items()
            if caller != function and function in callees.internal
        }
    )


def get_unstubbed_external_callees_for(
    function_to_verify: str,
    call_graph: CallGraph,
    stub_index: dict[str, Path],
) -> list[str]:
    """Return external callees of `function_to_verify` that have no CBMC stub.

    CBMC will treat these calls as nondeterministic.

    Args:
        function_to_verify (str): The function whose callees should be inspected.
        call_graph (dict[str, dict[str, list[str]]]): The call graph.
        stub_index (dict[str, Path]): Stub index produced by `build_stub_index`.

    Returns:
        list[str]: Sorted, de-duplicated list of external callee names not present in the stub
            index.
    """
    external_callees = call_graph.get_callees(function_to_verify).external
    return sorted({name for name in external_callees if name not in stub_index})
