# Ignore https://docs.astral.sh/ruff/rules/subclass-builtin/#subclass-builtin-furb189.
# ruff: noqa : FURB189

"""Class to represent a call graph."""

from __future__ import annotations


class CallGraphCallees(dict):
    """Represent callees in a call graph.

    Callees are split into `internal` (defined in the same file) and `external` (everything else —
    typically libc or other library calls). Downstream callers use this split to decide what to pass
    to CBMC's `--replace-call-with-contract` flag, which only makes sense for in-file callees.

    Subclasses `dict` so instances are JSON-serializable directly via `json.dumps`.

    Attributes:
        internal (list[str]): Internal (i.e., same-file) callees.
        external (list[str]): External callees.
    """

    def __init__(self, internal: list[str], external: list[str]) -> None:
        """Create a CallGraphCallees instance."""
        super().__init__(internal=list(internal), external=list(external))

    @property
    def internal(self) -> list[str]:
        """The internal (same-file) callees."""
        return self["internal"]

    @property
    def external(self) -> list[str]:
        """The external (library) callees."""
        return self["external"]


class CallGraph(dict):
    """Represent a call graph.

    A `CallGraph` is a `dict` mapping function name to its `CallGraphCallees`. Subclassing `dict`
    makes instances JSON-serializable directly via `json.dumps`.
    """

    def __init__(self, callgraph: dict[str, dict[str, list[str]]]) -> None:
        """Create a CallGraph instance."""
        super().__init__()
        for function, callees in callgraph.items():
            self[function] = CallGraphCallees(callees["internal"], callees["external"])

    def get_callees(self, function: str) -> CallGraphCallees:
        """Return the callees of the given function.

        Args:
            function (str): The function for which to return callees.

        Returns:
            CallGraphCallees: The callees of the given function.
        """
        if function not in self:
            msg = f"'{function}' was missing from the call graph"
            raise ValueError(msg)
        return self[function]

    def is_self_recursive(self, function: str) -> bool:
        """Return True iff the given function is self-recursive.

        This checks for self-recursive function; mutually-recursive functions are not handled.

        Args:
            function (str): The function to check for self-recursion.

        Returns:
            bool: True iff the function is self-recursive.
        """
        external_and_internal_callees = self[function]
        return (
            function
            in external_and_internal_callees["internal"] + external_and_internal_callees["external"]
        )

    def size(self) -> int:
        """Return the number of functions in this call graph.

        Returns:
            int: The number of functions in this call graph.
        """
        return len(self)
