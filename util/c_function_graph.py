from __future__ import annotations

from pathlib import Path

import networkx as nx
from loguru import logger

from util.c_function import CFunction
from util.tree_sitter_util import get_call_sites, parse_file


class CFunctionGraph:
    """Whole-program call graph for a C repository.

    Nodes are CFunction instances. Edges point from caller to callee.
    """

    def __init__(self, input_path: Path) -> None:
        self._graph: nx.DiGraph = nx.DiGraph()
        self._by_name: dict[str, CFunction] = {}
        self._build(input_path)

    def _build(self, input_path: Path) -> None:
        c_files = sorted(input_path.rglob("*.c"))
        if not c_files:
            logger.warning(f"No .c files found under {input_path}")
            return

        for f in c_files:
            for fn in parse_file(f):
                if fn.name in self._by_name:
                    logger.warning(
                        f"Duplicate function name '{fn.name}' — keeping first definition"
                    )
                    continue
                self._by_name[fn.name] = fn
                self._graph.add_node(fn)

        for fn in list(self._by_name.values()):
            for callee_name in get_call_sites(fn.file_path, fn.name):
                if callee_name in self._by_name:
                    self._graph.add_edge(fn, self._by_name[callee_name])

        logger.info(
            f"Built call graph: {self._graph.number_of_nodes()} functions, "
            f"{self._graph.number_of_edges()} edges"
        )

    def get_function(self, name: str) -> CFunction:
        return self._by_name[name]

    def get_function_or_none(self, name: str) -> CFunction | None:
        return self._by_name.get(name)

    def all_functions(self) -> list[CFunction]:
        return list(self._by_name.values())

    def get_callees(self, fn: CFunction) -> list[CFunction]:
        return list(self._graph.successors(fn))

    def get_callers(self, fn: CFunction) -> list[CFunction]:
        return list(self._graph.predecessors(fn))

    def topological_order(self) -> list[CFunction]:
        """Return functions leaves-first (callees before callers).

        Strongly-connected components (mutual recursion) are collapsed and
        returned as a group before their callers.
        """
        condensed = nx.condensation(self._graph)
        ordered: list[CFunction] = []
        for scc_id in reversed(list(nx.topological_sort(condensed))):
            members = condensed.nodes[scc_id]["members"]
            ordered.extend(members)
        return ordered

    def is_recursive(self, fn: CFunction) -> bool:
        """True if fn is part of a cycle (directly or mutually recursive)."""
        try:
            nx.find_cycle(self._graph, source=fn)
            return True
        except nx.NetworkXNoCycle:
            return False
