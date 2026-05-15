#!/usr/bin/env -S uv run --quiet python3

"""Print an aggregate table summarizing an evaluate_specification_quality.py JSONL stream.

Usage:
    % ./eval/visualization/summarize_spec_quality.py <JSONL> [--json]
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from eval.visualization.util import (
    SpecificationQualitySummary,
    group_by_summary_type,
    yield_records,
)


def _mut_stats(records: list[dict]) -> dict:
    if not records:
        return {"count": 0}
    scores = [r["kill_score"] for r in records]
    return {
        "count": len(records),
        "mean_kill_score": round(statistics.fmean(scores), 4),
        "median_kill_score": round(statistics.median(scores), 4),
        "min_kill_score": round(min(scores), 4),
        "max_kill_score": round(max(scores), 4),
        "total_killed": sum(r["killed"] for r in records),
        "total_mutants": sum(r["total"] for r in records),
    }


def _red_stats(records: list[dict]) -> dict:
    if not records:
        return {"count": 0}
    iso = [r["in_isolation_redundancy_rate"] for r in records]
    cal = [r["caller_side_redundancy_rate"] for r in records]
    return {
        "count": len(records),
        "mean_in_isolation_rate": round(statistics.fmean(iso), 4),
        "mean_caller_side_rate": round(statistics.fmean(cal), 4),
        "total_unobservable": sum(r.get("num_unobservable", 0) for r in records),
        "total_unverifiable_baseline": sum(r.get("num_unverifiable_baseline", 0) for r in records),
    }


def _joinable(mutation: list[dict], redundancy: list[dict]) -> int:
    keys_m = {(r["file"], r["function"]) for r in mutation}
    keys_r = {(r["file"], r["function"]) for r in redundancy}
    return len(keys_m & keys_r)


def _format_table(mut: dict, red: dict, joinable: int) -> str:
    rows = [("Metric", "Value")]

    def push_section(title: str, stats: dict) -> None:
        rows.append((title, ""))
        if stats["count"] == 0:
            rows.append(("  (no records)", ""))
            return
        for key, value in stats.items():
            rows.append((f"  {key}", str(value)))

    push_section("mutation_summary", mut)
    push_section("clause_redundancy_summary", red)
    rows.append(("functions in both streams", str(joinable)))

    key_width = max(len(k) for k, _ in rows)
    val_width = max(len(v) for _, v in rows)
    sep = "-" * (key_width + val_width + 3)
    lines: list[str] = [sep]
    for k, v in rows:
        lines.append(f"{k.ljust(key_width)} | {v.rjust(val_width)}")
        if k == "Metric":
            lines.append(sep)
    lines.append(sep)
    return "\n".join(lines)


def main() -> None:
    """Print an aggregate table summarizing an evaluate_specification_quality.py JSONL stream."""
    parser = argparse.ArgumentParser(
        "Print an aggregate table summarizing an evaluate_specification_quality.py JSONL stream."
    )
    parser.add_argument(
        "jsonl", help="Path to the JSONL produced by evaluate_specification_quality.py"
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit the aggregate as a single JSON object."
    )
    args = parser.parse_args()

    grouped_records = group_by_summary_type(yield_records(args.jsonl))
    mutation_records = (
        grouped_records.get(SpecificationQualitySummary.SUMMARY_FOR_MUTATION_TESTING) or []
    )
    redundancy_records = (
        grouped_records.get(SpecificationQualitySummary.SUMMARY_FOR_CLAUSE_REDUNDANCY) or []
    )
    aggregate = {
        "mutation_summary": _mut_stats(mutation_records),
        "clause_redundancy_summary": _red_stats(redundancy_records),
        "functions_in_both_streams": _joinable(mutation_records, redundancy_records),
    }

    if args.json:
        print(json.dumps(aggregate, indent=4))
    else:
        print(
            _format_table(
                aggregate["mutation_summary"],
                aggregate["clause_redundancy_summary"],
                aggregate["functions_in_both_streams"],
            )
        )
    sys.exit(0)


if __name__ == "__main__":
    sys.exit(main())
