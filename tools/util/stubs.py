"""Helpers for resolving CBMC stub files needed to verify a function."""

from __future__ import annotations

import json
import re
from pathlib import Path

_STUBS_DIR = Path(__file__).resolve().parents[2] / "stubs"

# CBMC stub files mark each modeled symbol with a `/* FUNCTION: <name> */` comment immediately
# preceding the C definition.
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
    path_to_call_graph: str,
    stub_index: dict[str, Path],
) -> list[str]:
    """Return the stub file paths needed to verify `function_to_verify`.

    External callees come from the call graph's `external` list for `function_to_verify`. Each
    external callee is looked up in the stub index; unresolved names are dropped.

    Args:
        function_to_verify (str): The function whose callees should be resolved.
        path_to_call_graph (str): Path to the JSON call graph emitted by `construct_call_graph`.
        stub_index (dict[str, Path]): Stub index produced by `build_stub_index`.

    Returns:
        list[str]: Sorted, de-duplicated list of stub file paths.
    """
    call_graph: dict[str, dict[str, list[str]]] = json.loads(Path(path_to_call_graph).read_text())
    external = call_graph.get(function_to_verify, {}).get("external", [])
    resolved = {stub_index[name] for name in external if name in stub_index}
    return sorted(str(path) for path in resolved)


def get_in_file_callees_for(
    function_to_verify: str,
    path_to_call_graph: str,
) -> list[str]:
    """Return direct callees of `function_to_verify` that are defined in the same C file.

    These are the candidates to pass to CBMC via `--replace-call-with-contract`. The function
    itself is excluded so a self-recursive call doesn't get rewritten into a contract call.

    Args:
        function_to_verify (str): The function whose callees should be resolved.
        path_to_call_graph (str): Path to the JSON call graph emitted by `construct_call_graph`.

    Returns:
        list[str]: Sorted, de-duplicated list of in-file callee names.
    """
    call_graph: dict[str, dict[str, list[str]]] = json.loads(Path(path_to_call_graph).read_text())
    internal = call_graph.get(function_to_verify, {}).get("internal", [])
    return sorted({name for name in internal if name != function_to_verify})


def get_unstubbed_external_callees_for(
    function_to_verify: str,
    path_to_call_graph: str,
    stub_index: dict[str, Path],
) -> list[str]:
    """Return external callees of `function_to_verify` that have no CBMC stub.

    These are calls CBMC will treat as nondeterministic: the function is declared but neither
    defined in the file under verification nor modeled in `stubs/`, so CBMC returns an arbitrary
    value with no constraints on side effects beyond what frame inference can prove.

    Args:
        function_to_verify (str): The function whose callees should be inspected.
        path_to_call_graph (str): Path to the JSON call graph emitted by `construct_call_graph`.
        stub_index (dict[str, Path]): Stub index produced by `build_stub_index`.

    Returns:
        list[str]: Sorted, de-duplicated list of external callee names not present in the stub
            index.
    """
    call_graph: dict[str, dict[str, list[str]]] = json.loads(Path(path_to_call_graph).read_text())
    external = call_graph.get(function_to_verify, {}).get("external", [])
    return sorted({name for name in external if name not in stub_index})
