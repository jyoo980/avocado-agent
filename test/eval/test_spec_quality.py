"""Tests for the eval/spec_quality.py orchestrator (file-discovery + counterpart resolution)."""

from __future__ import annotations

import json
import sys
from io import StringIO
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from eval import spec_quality  # noqa: E402
from eval.spec_quality import _collect_c_files, _process_file, _resolve_counterpart  # noqa: E402


def _write_c(tmp_path: Path, name: str, source: str) -> Path:
    path = tmp_path / name
    path.write_text(source)
    return path


def test_collect_c_files_handles_single_file(tmp_path: Path) -> None:
    path = _write_c(tmp_path, "x.c", "int main(void){return 0;}")
    assert _collect_c_files(str(path)) == [path]


def test_collect_c_files_walks_directory(tmp_path: Path) -> None:
    a = _write_c(tmp_path, "a.c", "int f(void){return 0;}")
    sub = tmp_path / "sub"
    sub.mkdir()
    b = _write_c(sub, "b.c", "int g(void){return 0;}")
    _write_c(tmp_path, "ignored.txt", "")
    files = _collect_c_files(str(tmp_path))
    assert sorted(files) == sorted([a, b])


def test_resolve_counterpart_matches_basename(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    gt_dir = tmp_path / "gt"
    src_dir.mkdir()
    gt_dir.mkdir()
    src_file = _write_c(src_dir, "csv.c", "")
    gt_file = _write_c(gt_dir, "csv.c", "")
    assert _resolve_counterpart(src_file, gt_dir) == gt_file


def test_resolve_counterpart_handles_file_root(tmp_path: Path) -> None:
    src = _write_c(tmp_path, "src.c", "")
    gt = _write_c(tmp_path, "gt.c", "")
    assert _resolve_counterpart(src, gt) == gt


def test_resolve_counterpart_returns_none_when_missing(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    gt_dir = tmp_path / "gt"
    src_dir.mkdir()
    gt_dir.mkdir()
    src_file = _write_c(src_dir, "alpha.c", "")
    _write_c(gt_dir, "beta.c", "")
    assert _resolve_counterpart(src_file, gt_dir) is None


def test_process_file_emits_static_metrics_records(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = _write_c(
        tmp_path,
        "ok.c",
        """
        int g(int a)
        __CPROVER_requires(a > 0)
        __CPROVER_ensures(__CPROVER_return_value == a)
        { return a; }
        """,
    )
    out = StringIO()
    monkeypatch.setattr(spec_quality, "score_function_mutations", lambda *a, **k: None)
    _process_file(
        source=source,
        out=out,
        run_static=True,
        run_mutation=False,
        run_redundancy=False,
        run_replaceability=False,
        compare_root=None,
    )
    records = [json.loads(line) for line in out.getvalue().splitlines() if line]
    assert len(records) == 1
    assert records[0]["kind"] == "static_metrics"
    assert records[0]["function"] == "g"


def test_process_file_runs_mutation_per_annotated_function(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = _write_c(
        tmp_path,
        "two.c",
        """
        int f(int a)
        __CPROVER_requires(a > 0)
        __CPROVER_ensures(__CPROVER_return_value == a)
        { return a; }

        int g(int b)
        __CPROVER_requires(b > 0)
        __CPROVER_ensures(__CPROVER_return_value == b)
        { return b; }
        """,
    )
    invoked: list[str] = []

    class _FakeScore:
        def __init__(self, function: str) -> None:
            self.file = str(source)
            self.function = function
            self.total = 0
            self.killed = 0
            self.survived = 0
            self.kill_rate = 0.0

    def fake_score(file_path: str, function_name: str) -> _FakeScore:
        invoked.append(function_name)
        return _FakeScore(function_name)

    monkeypatch.setattr(spec_quality, "score_function_mutations", fake_score)
    out = StringIO()
    _process_file(
        source=source,
        out=out,
        run_static=False,
        run_mutation=True,
        run_redundancy=False,
        run_replaceability=False,
        compare_root=None,
    )
    assert sorted(invoked) == ["f", "g"]
    records = [json.loads(line) for line in out.getvalue().splitlines() if line]
    assert all(r["kind"] == "mutation_summary" for r in records)
