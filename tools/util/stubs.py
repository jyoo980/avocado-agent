"""Helpers for resolving CBMC stub files needed to verify a function."""

from __future__ import annotations

import re
from pathlib import Path

_STUBS_DIR = Path(__file__).resolve().parents[2] / "stubs"

# CBMC stub files mark each modeled symbol with a `/* FUNCTION: <name> */` comment immediately
# preceding the C definition. The C identifier itself is often a renamed/prefixed alias (e.g.
# `_avocado_printf`), so the comment marker is the source of truth for the symbol name.
_FUNCTION_MARKER = re.compile(r"/\*\s*FUNCTION:\s*(\S+)\s*\*/")


def build_stub_index(stubs_dir: Path = _STUBS_DIR) -> dict[str, Path]:
    """Return a mapping from each modeled function name to the stub file defining it.

    Symbol names are read from `/* FUNCTION: <name> */` markers. If a name appears in more than
    one stub file, the first one encountered (in sorted-path order) wins.

    Args:
        stubs_dir (Path): The directory containing CBMC stub `.c` files.

    Returns:
        dict[str, Path]: Mapping of function name to the stub file that defines it.
    """
    index: dict[str, Path] = {}
    for stub_path in sorted(stubs_dir.glob("*.c")):
        for name in _FUNCTION_MARKER.findall(stub_path.read_text(encoding="utf-8")):
            index.setdefault(name, stub_path)
    return index


def resolve_stub_paths_for(
    function_to_verify: str,
    call_graph: dict[str, dict[str, list[str]]],
    stub_index: dict[str, Path],
) -> list[str]:
    """Return the stub file paths needed to verify `function_to_verify`.

    External callees come from the call graph's `external` list for `function_to_verify`. Each
    external callee is looked up in the stub index; unresolved names are dropped.

    Args:
        function_to_verify (str): The function whose callees should be resolved.
        call_graph (dict[str, dict[str, list[str]]]): The call graph.
        stub_index (dict[str, Path]): Stub index produced by `build_stub_index`.

    Returns:
        list[str]: Sorted, de-duplicated list of stub file paths.
    """
    external = get_call_graph_entry(function_to_verify, call_graph).get("external", [])
    resolved = {stub_index[name] for name in external if name in stub_index}
    return sorted(str(path) for path in resolved)


def get_in_file_callees_for(
    function_to_verify: str,
    call_graph: dict[str, dict[str, list[str]]],
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

    Args:
        function_to_verify (str): The function whose callees should be resolved.
        call_graph (dict[str, dict[str, list[str]]]): The call graph.
        include_self (bool): When True, keep `function_to_verify` in the result if it calls
            itself. Defaults to False.

    Returns:
        list[str]: Sorted, de-duplicated list of in-file callee names.
    """
    internal = call_graph.get(function_to_verify, {}).get("internal", [])
    if include_self:
        return sorted(set(internal))
    return sorted({name for name in internal if name != function_to_verify})


def get_unstubbed_external_callees_for(
    function_to_verify: str,
    call_graph: dict[str, dict[str, list[str]]],
    stub_index: dict[str, Path],
) -> list[str]:
    """Return external callees of `function_to_verify` that have no CBMC stub.

    These are calls CBMC will treat as nondeterministic: the function is declared but neither
    defined in the file under verification nor modeled in `stubs/`, so CBMC returns an arbitrary
    value with no constraints on side effects beyond what frame inference can prove.

    Args:
        function_to_verify (str): The function whose callees should be inspected.
        call_graph (dict[str, dict[str, list[str]]]): The call graph.
        stub_index (dict[str, Path]): Stub index produced by `build_stub_index`.

    Returns:
        list[str]: Sorted, de-duplicated list of external callee names not present in the stub
            index.
    """
    external = get_call_graph_entry(function_to_verify, call_graph).get("external", [])
    return sorted({name for name in external if name not in stub_index})


def get_call_graph_entry(
    function: str, call_graph: dict[str, dict[str, list[str]]]
) -> dict[str, list]:
    """Return the call graph entry for a function.

    Args:
        function (str): The function for which to return the call graph entry.
        call_graph (dict[str, dict[str, list[str]]]): The call graph from which to fetch the
            function's call graph entry.

    Returns:
        dict[str, list[str]]: The call graph entry for a function comprising a map of its callees.
    """
    if call_graph_entry := call_graph.get(function):
        return call_graph_entry
    msg = f"Function '{function}' was missing from the call graph"
    raise ValueError(msg)
