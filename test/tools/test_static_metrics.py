"""Tests for tools/static_metrics.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.static_metrics import compute_static_metrics


def _write_c(tmp_path: Path, source: str) -> str:
    path = tmp_path / "fixture.c"
    path.write_text(source)
    return str(path)


def test_unannotated_functions_are_skipped(tmp_path: Path) -> None:
    file_path = _write_c(
        tmp_path,
        """
        int plain_function(int x) {
            return x + 1;
        }
        """,
    )
    assert compute_static_metrics(file_path) == []


def test_vacuity_flags_trivial_requires_true(tmp_path: Path) -> None:
    file_path = _write_c(
        tmp_path,
        """
        int trivially_specced(int x)
        __CPROVER_requires(1)
        __CPROVER_ensures(__CPROVER_return_value == x)
        {
            return x;
        }
        """,
    )
    metrics = compute_static_metrics(file_path)[0]
    assert "__CPROVER_requires(1)" in " ".join(metrics.vacuity.trivial_clauses)


def test_vacuity_flags_self_equal_binary_expression(tmp_path: Path) -> None:
    file_path = _write_c(
        tmp_path,
        """
        int self_equal(int x)
        __CPROVER_requires(x == x)
        __CPROVER_ensures(__CPROVER_return_value == x)
        {
            return x;
        }
        """,
    )
    metrics = compute_static_metrics(file_path)[0]
    assert any("x == x" in c for c in metrics.vacuity.trivial_clauses)


def test_vacuity_flags_empty_assigns_when_body_writes(tmp_path: Path) -> None:
    file_path = _write_c(
        tmp_path,
        """
        void writes_through_pointer(int *p)
        __CPROVER_requires(__CPROVER_is_fresh(p, sizeof(int)))
        __CPROVER_assigns()
        {
            *p = 42;
        }
        """,
    )
    metrics = compute_static_metrics(file_path)[0]
    assert metrics.vacuity.empty_assigns_despite_writes is True


def test_lexical_overlap_intersects_only_program_identifiers(tmp_path: Path) -> None:
    file_path = _write_c(
        tmp_path,
        """
        int add(int x, int y)
        __CPROVER_requires(x >= 0 && y >= 0)
        __CPROVER_ensures(__CPROVER_return_value == x + y)
        {
            int result = x + y;
            return result;
        }
        """,
    )
    metrics = compute_static_metrics(file_path)[0]
    # spec mentions {x, y}; body mentions {x, y, result} — overlap is 2/3.
    assert metrics.lexical_overlap.spec_identifier_count == 2
    assert metrics.lexical_overlap.body_identifier_count == 3
    assert metrics.lexical_overlap.intersection_count == 2


def test_concrete_value_bias_only_counts_ensures_terms(tmp_path: Path) -> None:
    file_path = _write_c(
        tmp_path,
        """
        int constant_returner(int x)
        __CPROVER_requires(x == 42)
        __CPROVER_ensures(__CPROVER_return_value == 7)
        {
            return 7;
        }
        """,
    )
    metrics = compute_static_metrics(file_path)[0]
    # The requires literal `42` must NOT be counted; only the ensures literal `7`.
    assert metrics.concrete_value_bias.literal_count == 1
    # __CPROVER_return_value is filtered as spec vocabulary, so no symbolic terms.
    assert metrics.concrete_value_bias.symbolic_count == 0
    assert metrics.concrete_value_bias.literal_ratio == 1.0


def test_return_value_coverage_flags_missing_postcondition(tmp_path: Path) -> None:
    file_path = _write_c(
        tmp_path,
        """
        int unconstrained_return(int x)
        __CPROVER_requires(x > 0)
        {
            return x * 2;
        }
        """,
    )
    metrics = compute_static_metrics(file_path)[0]
    assert metrics.return_value_coverage.is_void is False
    assert metrics.return_value_coverage.ensures_references_return_value is False


def test_return_value_coverage_recognizes_void(tmp_path: Path) -> None:
    file_path = _write_c(
        tmp_path,
        """
        void no_return(int *p)
        __CPROVER_requires(__CPROVER_is_fresh(p, sizeof(int)))
        __CPROVER_assigns(*p)
        __CPROVER_ensures(*p == 0)
        {
            *p = 0;
        }
        """,
    )
    metrics = compute_static_metrics(file_path)[0]
    assert metrics.return_value_coverage.is_void is True
    assert metrics.return_value_coverage.ensures_references_return_value is False


def test_pointer_safety_coverage_reports_uncovered_dereferenced_param(tmp_path: Path) -> None:
    file_path = _write_c(
        tmp_path,
        """
        int read_through(int *covered, int *uncovered)
        __CPROVER_requires(__CPROVER_is_fresh(covered, sizeof(int)))
        __CPROVER_ensures(__CPROVER_return_value == *covered + *uncovered)
        {
            return *covered + *uncovered;
        }
        """,
    )
    metrics = compute_static_metrics(file_path)[0]
    assert sorted(metrics.pointer_safety_coverage.pointer_params) == ["covered", "uncovered"]
    assert sorted(metrics.pointer_safety_coverage.dereferenced_in_body) == ["covered", "uncovered"]
    assert metrics.pointer_safety_coverage.covered_in_spec == ["covered"]
    assert metrics.pointer_safety_coverage.uncovered == ["uncovered"]


def test_pointer_safety_coverage_recognizes_array_decay(tmp_path: Path) -> None:
    file_path = _write_c(
        tmp_path,
        """
        int read_first(int arr[], int n)
        __CPROVER_requires(n > 0)
        __CPROVER_requires(__CPROVER_is_fresh(arr, n * sizeof(int)))
        __CPROVER_ensures(__CPROVER_return_value == arr[0])
        {
            return arr[0];
        }
        """,
    )
    metrics = compute_static_metrics(file_path)[0]
    assert "arr" in metrics.pointer_safety_coverage.pointer_params
    assert "arr" in metrics.pointer_safety_coverage.dereferenced_in_body
    assert "arr" in metrics.pointer_safety_coverage.covered_in_spec


def test_feature_histogram_counts_predicates_and_clauses(tmp_path: Path) -> None:
    file_path = _write_c(
        tmp_path,
        """
        int sum_array(int arr[], int n)
        __CPROVER_requires(n >= 0 && n < 100)
        __CPROVER_requires(__CPROVER_is_fresh(arr, n * sizeof(int)))
        __CPROVER_assigns(__CPROVER_object_whole(arr))
        __CPROVER_ensures(__CPROVER_forall { int k; (0 <= k && k < n) ==> arr[k] >= 0 })
        {
            int total = 0;
            for (int i = 0; i < n; i++) total += arr[i];
            return total;
        }
        """,
    )
    metrics = compute_static_metrics(file_path)[0]
    hist = metrics.feature_histogram
    assert hist["__CPROVER_requires"] == 2
    assert hist["__CPROVER_assigns"] == 1
    assert hist["__CPROVER_ensures"] == 1
    assert hist["__CPROVER_is_fresh"] == 1
    assert hist["__CPROVER_object_whole"] == 1
    assert hist["__CPROVER_forall"] == 1


def test_compute_static_metrics_skips_files_without_any_annotations(tmp_path: Path) -> None:
    file_path = _write_c(
        tmp_path,
        """
        int helper(int a, int b) { return a + b; }
        void other(void) {}
        """,
    )
    assert compute_static_metrics(file_path) == []


def test_quickSort_benchmark_smoke(tmp_path: Path) -> None:
    """Sanity check on the in-repo quicksort benchmark — just confirms shape, not numbers."""
    repo_quicksort = Path(__file__).resolve().parents[2] / "eval/benchmarks/quicksort/quicksort.c"
    if not repo_quicksort.exists():
        pytest.skip("quicksort benchmark not present")
    metrics = {m.function: m for m in compute_static_metrics(str(repo_quicksort))}
    assert {"swap", "partition", "quickSort"} <= set(metrics.keys())
    # `swap` returns void and asserts is_fresh on both pointer params.
    assert metrics["swap"].return_value_coverage.is_void is True
    assert metrics["swap"].pointer_safety_coverage.uncovered == []
    # `partition` returns int and constrains the return value.
    assert metrics["partition"].return_value_coverage.is_void is False
    assert metrics["partition"].return_value_coverage.ensures_references_return_value is True
