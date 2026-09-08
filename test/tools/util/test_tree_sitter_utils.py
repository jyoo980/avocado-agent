"""Tests for tree_sitter utility functions."""

import json
from pathlib import Path

from loguru import logger

from tools.util import CallGraphCallees
from tools.util import tree_sitter_utils


def test_construct_call_graph_only_leaf_nodes() -> None:
    call_graph = tree_sitter_utils.get_call_graph("test/data/no_callees.c")
    assert call_graph.size() == 3, (
        f"Expected 3 functions in simple.c, but got {call_graph.size()}"
    )
    functions = ["foo", "bar", "baz"]
    for f in functions:
        assert call_graph.get_callees(f) == CallGraphCallees(
            internal=[], external=[]
        ), (
            f"Expected '{f}' in simple.c to have no callees, but got {call_graph.get_callees(f)}"
        )


def test_construct_call_graph_with_callees_no_libraries() -> None:
    call_graph = tree_sitter_utils.get_call_graph("test/data/quicksort.c")
    assert call_graph.size() == 3, (
        f"Expected 3 functions in quicksort.c, but got {call_graph.size()}"
    )
    assert call_graph.get_callees("swap") == CallGraphCallees(
        internal=[], external=[]
    ), (
        f"Expected 'swap' in quicksort.c' to have no callees, but got {call_graph.get_callees('swap')}"
    )
    partition_callees = call_graph.get_callees("partition")
    assert partition_callees.internal == ["swap"], (
        f"Expected 'partition' in quicksort.c' to have internal callee 'swap'"
    )

    quicksort_callees = call_graph.get_callees("quickSort")
    for callee in ["quickSort", "partition"]:
        assert callee in quicksort_callees.internal, (
            f"Expected '{callee}' to be an internal callee of quickSort"
        )


def test_construct_call_graph_matches_checked_in_artifact() -> None:
    # `construct_call_graph` caches its output next to the source; the checked-in artifact for
    # quicksort.c must stay in sync with what `get_call_graph` produces.
    call_graph = tree_sitter_utils.get_call_graph("test/data/quicksort.c")
    expected = json.loads(Path("test/data/quicksort-callgraph.json").read_text())
    assert dict(call_graph) == expected, (
        f"Expected the call graph of quicksort.c to match the checked-in artifact, got {call_graph}"
    )


def test_construct_call_graph_recovers_function_with_cprover_forall_clause() -> None:
    # `__CPROVER_forall { int k; ... }` braces confuse tree-sitter into wrapping the surrounding
    # function in an ERROR node rather than a function_definition. The call-graph build must
    # recover that ERROR-wrapped definition; otherwise the function disappears and its callers
    # mis-classify it as external.
    call_graph = tree_sitter_utils.get_call_graph("test/data/quicksort_with_forall.c")
    assert call_graph.size() == 3, (
        f"Expected 3 functions in quicksort_with_forall.c, but got {call_graph.size()}"
    )

    partition_callees = call_graph.get_callees("partition")
    assert partition_callees.internal == ["swap"], (
        f"Expected 'partition' to have internal callee 'swap', got {partition_callees}"
    )

    quicksort_callees = call_graph.get_callees("quickSort")
    for callee in ["quickSort", "partition"]:
        assert callee in quicksort_callees.internal, (
            f"Expected '{callee}' to be an internal callee of quickSort, got {quicksort_callees}"
        )
    assert "partition" not in quicksort_callees.external, (
        "ERROR-wrapped 'partition' must not be mis-classified as external"
    )


def test_construct_call_graph_recovers_function_with_cprover_forall_and_subscript_clause() -> (
    None
):
    # Subscript syntax inside a `__CPROVER_forall` clause (e.g., `arr[k]`) nests the real
    # `function_declarator` under one or more `array_declarator` wrappers inside the ERROR node,
    # rather than as a direct child. The call-graph build must still recover the function.
    call_graph = tree_sitter_utils.get_call_graph(
        "test/data/quicksort_with_forall_subscript.c"
    )
    assert call_graph.size() == 3, (
        f"Expected 3 functions in quicksort_with_forall_subscript.c, but got {call_graph.size()}"
    )

    partition_callees = call_graph.get_callees("partition")
    assert partition_callees.internal == ["swap"], (
        f"Expected 'partition' to have internal callee 'swap', got {partition_callees}"
    )

    quicksort_callees = call_graph.get_callees("quickSort")
    for callee in ["quickSort", "partition"]:
        assert callee in quicksort_callees.internal, (
            f"Expected '{callee}' to be an internal callee of quickSort, got {quicksort_callees}"
        )
    assert "partition" not in quicksort_callees.external, (
        "ERROR-wrapped 'partition' must not be mis-classified as external"
    )


def test_get_functions_with_cprover_annos() -> None:
    fns_with_annos = tree_sitter_utils.get_functions_with_cprover_annotations(
        "test/data/pointer_decl_function.c"
    )

    assert len(fns_with_annos) == 2, (
        f"Expected two functions with annotations, but got {len(fns_with_annos)}"
    )
    assert "swap" in fns_with_annos and "bin2hex" in fns_with_annos, (
        f"Expected 'swap' and 'bin2hex' in {fns_with_annos}"
    )


def test_construct_call_graph_recovers_names_of_macro_prefixed_functions() -> None:
    # `API_MACRO err_t update (...)`: tree-sitter's error recovery may pick `err_t` as the
    # function name and dump `update` into an ERROR node. The real name is the identifier right
    # before the parameter list.
    call_graph = tree_sitter_utils.get_call_graph("test/data/macro_prefixed_decl.c")
    expected = {"helper", "update", "update64", "decompress_generic", "first_byte"}
    assert set(call_graph) == expected, (
        f"Expected functions {sorted(expected)} in macro_prefixed_decl.c, got {sorted(call_graph)}"
    )
    assert call_graph.get_callees("update").internal == ["helper"], (
        f"Expected 'update' to call 'helper', got {call_graph.get_callees('update')}"
    )
    assert call_graph.get_callees("update64").internal == ["update"], (
        f"Expected 'update64' to call 'update', got {call_graph.get_callees('update64')}"
    )
    assert call_graph.get_callees("decompress_generic").internal == ["helper"], (
        f"Expected 'decompress_generic' to call 'helper', "
        f"got {call_graph.get_callees('decompress_generic')}"
    )


def test_construct_call_graph_survives_labels_inside_in_body_conditionals() -> None:
    # `#if FAST_LOOP / safe_copy: / #endif` inside a body makes the label swallow the `#endif`;
    # without blanking, the function (and everything after it) is lost.
    call_graph = tree_sitter_utils.get_call_graph("test/data/label_in_conditional.c")
    assert set(call_graph) == {"decode", "decode_all"}, (
        f"Expected 'decode' and 'decode_all' in label_in_conditional.c, got {sorted(call_graph)}"
    )
    assert call_graph.get_callees("decode") == CallGraphCallees(internal=[], external=[]), (
        f"Expected 'decode' to have no callees, got {call_graph.get_callees('decode')}"
    )
    assert call_graph.get_callees("decode_all").internal == ["decode"], (
        f"Expected 'decode_all' to call 'decode', got {call_graph.get_callees('decode_all')}"
    )


def test_construct_call_graph_ignores_if_zero_dead_code() -> None:
    call_graph = tree_sitter_utils.get_call_graph("test/data/dead_branch_calls.c")
    assert "dead_fn" not in call_graph, (
        f"Expected the `#if 0` definition 'dead_fn' to be ignored, got {sorted(call_graph)}"
    )
    hash_callees = call_graph.get_callees("hash")
    assert "reset" not in hash_callees.internal, (
        f"Expected calls in `#if 0` branches to be ignored, got {hash_callees}"
    )
    # Branches with unknown or non-zero conditions are all kept (union semantics).
    assert hash_callees.internal == ["late", "live", "mid"], (
        f"Expected 'hash' to call 'late', 'live' and 'mid', got {hash_callees}"
    )


def test_construct_call_graph_unions_callees_of_duplicate_definitions() -> None:
    warnings: list[str] = []
    handler_id = logger.add(warnings.append, level="WARNING", format="{message}")
    try:
        call_graph = tree_sitter_utils.get_call_graph("test/data/duplicate_definitions.c")
    finally:
        logger.remove(handler_id)

    assert set(call_graph) == {"read32", "hash"}, (
        f"Expected 'read32' and 'hash' in duplicate_definitions.c, got {sorted(call_graph)}"
    )
    assert call_graph.get_callees("read32").external == ["memcpy"], (
        f"Expected the union of callees of all 'read32' definitions, "
        f"got {call_graph.get_callees('read32')}"
    )
    assert call_graph.get_callees("hash").internal == ["read32"], (
        f"Expected 'hash' to call 'read32', got {call_graph.get_callees('hash')}"
    )
    assert any("'read32' is defined 3 times" in message for message in warnings), (
        f"Expected a warning about the duplicate definitions of 'read32', got {warnings}"
    )


def test_get_function_body_returns_first_definition_with_original_offsets() -> None:
    source = Path("test/data/duplicate_definitions.c").read_bytes()
    body = tree_sitter_utils.get_function_body("test/data/duplicate_definitions.c", "read32")
    assert body is not None, "Expected to find a body for 'read32'"
    assert body.start_point[0] == 5, (
        f"Expected the first definition of 'read32' (line 6), got line {body.start_point[0] + 1}"
    )
    text = source[body.start_byte : body.end_byte]
    assert text.startswith(b"{") and text.endswith(b"}"), (
        f"Expected body offsets to index the original source, got {text!r}"
    )


def test_get_function_body_of_macro_prefixed_function() -> None:
    body = tree_sitter_utils.get_function_body("test/data/macro_prefixed_decl.c", "update")
    assert body is not None, "Expected to find a body for the macro-prefixed 'update'"
    assert b"helper((int)len)" in (body.text or b""), (
        f"Expected the body of 'update', got {body.text!r}"
    )


def test_get_functions_with_cprover_annos_on_macro_prefixed_function(tmp_path: Path) -> None:
    source = Path("test/data/macro_prefixed_decl.c").read_text()
    annotated = source.replace(
        "API_MACRO err_t update (state_t* s, const void* input, size_t len)\n{",
        "API_MACRO err_t update (state_t* s, const void* input, size_t len)\n"
        "__CPROVER_requires(s != NULL)\n{",
    )
    assert annotated != source, "The fixture changed; update the replacement above"
    path = tmp_path / "annotated.c"
    path.write_text(annotated)

    fns_with_annos = tree_sitter_utils.get_functions_with_cprover_annotations(str(path))
    assert fns_with_annos == {"update"}, (
        f"Expected only 'update' to be annotated, got {fns_with_annos}"
    )


def test_parse_errors_are_reported() -> None:
    records: list[str] = []
    handler_id = logger.add(records.append, level="DEBUG", format="{level}: {message}")
    try:
        tree_sitter_utils._parse_to_ast(b"int f(void) { return 1; }\n}}} int (\n", label="broken.c")
        tree_sitter_utils._parse_to_ast(Path("test/data/quicksort.c").read_bytes(), label="ok.c")
    finally:
        logger.remove(handler_id)
    assert any("broken.c: tree-sitter reported parse errors" in record for record in records), (
        f"Expected the local parse errors of broken.c to be reported, got {records}"
    )
    assert not any("ok.c:" in record for record in records), (
        f"Expected no parse-error report for quicksort.c, got {records}"
    )
