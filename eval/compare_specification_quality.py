#!/usr/bin/env -S uv run --quiet python3

"""Compare two spec-quality JSONL logs and report per-function and aggregate deltas.

This script consumes the JSONL scripts produced by the `evaluate_specification_quality.py` script
for two experimental runs.

Usage:
    % ./eval/compare_spec_quality.py <BASELINE_JSONL> <experimental_JSONL> [--json]
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval.visualization.util import (
    SpecificationQualitySummary,
    group_by_summary_type,
    yield_records,
)

# Unique key comprising a (file, function) tuple.
FunctionKey = tuple[str, str]
_DEFAULT_NUM_DECIMAL_PLACES = 4


def main() -> None:
    """Compare two spec-quality JSONL logs and print the deltas."""
    parser = argparse.ArgumentParser(
        description="Compare two spec-quality JSONL logs (baseline vs experimental)."
    )
    parser.add_argument(
        "baseline", help=("JSONL for the baseline; output of `evaluate_specification_quality.py`.")
    )
    parser.add_argument(
        "experimental",
        help=(
            "JSONL for the experimental treatment; output of `evaluate_specification_quality.py`."
        ),
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit the comparison as a single JSON object."
    )
    args = parser.parse_args()

    report = _get_specification_quality_comparison(args.baseline, args.experimental)
    if args.json:
        print(json.dumps(report, indent=4))
    else:
        print(_format_report(report))


def _get_function_key_dict_from_list(records: list[dict]) -> dict[FunctionKey, dict]:
    """Return a dictionary where records are indexed by `FunctionKey`.

    Args:
        records (list[dict]): The items from the JSONL file to index by `FunctionKey`.

    Returns:
        dict[FunctionKey, dict]: A dictionary where records are indexed by `FunctionKey`.
    """
    return {(record["file"], record["function"]): record for record in records}


def _get_kill_score(record: dict) -> float:
    """Return the kill score obtained from a JSONL record.

    Args:
        record (dict): The record from which to obtain a kill score.

    Returns:
        float: The kill score obtained from a JSONL record.
    """
    if kill_score := record.get("kill_score"):
        return kill_score
    msg = f"Expected 'kill_score' in {record}"
    raise ValueError(msg)


def _get_kill_score_comparison(
    baseline_records: dict[FunctionKey, dict], experimental_records: dict[FunctionKey, dict]
) -> list[dict]:
    """Return the kill score comparison for functions in the baseline/experimental treatment.

    Args:
        baseline_records (dict[FunctionKey, dict]): The baseline records.
        experimental_records (dict[FunctionKey, dict]): The experimental treatment records.

    Returns:
        list[dict]: The kill score comparison for each function in the baseline and experimental
            treatment.
    """
    rows: list[dict] = []
    for key in baseline_records.keys() & experimental_records.keys():
        file_name, function = key
        baseline_record, experimental_record = baseline_records[key], experimental_records[key]
        rows.append(
            {
                "file": file_name,
                "function": function,
                "baseline": baseline_record,
                "experimental": experimental_record,
                "kill_score_delta": round(
                    _get_kill_score(experimental_record) - _get_kill_score(baseline_record),
                    _DEFAULT_NUM_DECIMAL_PLACES,
                ),
                "killed_delta": experimental_record["killed"] - baseline_record["killed"],
                "total_delta": experimental_record["total"] - baseline_record["total"],
            }
        )
    rows.sort(
        key=lambda kill_score_record: abs(kill_score_record["kill_score_delta"]), reverse=True
    )
    return rows


def _get_redundancy_score_comparison(
    baseline_records: dict[FunctionKey, dict], experimental_records: dict[FunctionKey, dict]
) -> list[dict]:
    """Return the redundancy score comparison for functions in the baseline/experimental treatment.

    Args:
        baseline_records (dict[FunctionKey, dict]): The baseline records.
        experimental_records (dict[FunctionKey, dict]): The experimental treatment records.

    Returns:
        list[dict]: The redundancy score comparison for each function in the baseline and
            experimental treatment.
    """
    rows: list[dict] = []
    for key in baseline_records.keys() & experimental_records.keys():
        file_name, function = key
        baseline_record, experimental_record = baseline_records[key], experimental_records[key]
        rows.append(
            {
                "file": file_name,
                "function": function,
                "baseline": baseline_record,
                "experimental": experimental_record,
                "in_isolation_redundancy_rate_delta": round(
                    experimental_record["in_isolation_redundancy_rate"]
                    - baseline_record["in_isolation_redundancy_rate"],
                    _DEFAULT_NUM_DECIMAL_PLACES,
                ),
                "caller_side_redundancy_rate_delta": round(
                    experimental_record["caller_side_redundancy_rate"]
                    - baseline_record["caller_side_redundancy_rate"],
                    _DEFAULT_NUM_DECIMAL_PLACES,
                ),
                "num_unobservable_delta": experimental_record.get("num_unobservable", 0)
                - baseline_record.get("num_unobservable", 0),
                "num_unverifiable_baseline_delta": experimental_record.get(
                    "num_unverifiable_baseline", 0
                )
                - baseline_record.get("num_unverifiable_baseline", 0),
            }
        )
    rows.sort(
        key=lambda redundancy_record: abs(redundancy_record["in_isolation_redundancy_rate_delta"]),
        reverse=True,
    )
    return rows


def _get_baseline_experimental_function_diffs(
    baseline_records: dict[FunctionKey, dict],
    experimental_records: dict[FunctionKey, dict],
    per_function: list[dict],
    headline_delta_key: str,
) -> dict[str, Any]:
    """Return the functions exclusive to either the baseline and experimental treatment(s).

    It may be the case that functions may not be present for all experimental treatments (e.g., some
    functions may time out in one treatment, while successfully verifying in others).

    Args:
        baseline_records (dict[FunctionKey, dict]): The baseline records.
        experimental_records (dict[FunctionKey, dict]): The experimental treatment records.
        per_function (list[dict]): Records per function.
        headline_delta_key (str): The delta key.

    Returns:
        dict[str, Any]: The diff between the experimental and the experimental records.
    """
    baseline_exclusive_functions = sorted(
        f"{f}#{fn}" for f, fn in baseline_records.keys() - experimental_records.keys()
    )
    experimental_exclusive_functions = sorted(
        f"{f}#{fn}" for f, fn in experimental_records.keys() - baseline_records.keys()
    )
    deltas = [row[headline_delta_key] for row in per_function]
    return {
        "baseline_count": len(baseline_records),
        "experimental_count": len(experimental_records),
        "common_count": len(per_function),
        "only_in_baseline": baseline_exclusive_functions,
        "only_in_experimental": experimental_exclusive_functions,
        f"mean_{headline_delta_key}": round(statistics.fmean(deltas), 4) if deltas else 0.0,
        "per_function": per_function,
    }


def _get_specification_quality_comparison(
    path_to_baseline_records: str, path_to_experimental_records: str
) -> dict[str, Any]:
    baseline_spec_quality_type_to_records = group_by_summary_type(
        yield_records(path_to_baseline_records)
    )
    experimental_spec_quality_type_to_records = group_by_summary_type(
        yield_records(path_to_experimental_records)
    )

    baseline_mutation_testing_summaries = _get_function_key_dict_from_list(
        baseline_spec_quality_type_to_records.get(
            SpecificationQualitySummary.SUMMARY_FOR_MUTATION_TESTING
        )
        or []
    )
    experimental_mutation_testing_summaries = _get_function_key_dict_from_list(
        experimental_spec_quality_type_to_records.get(
            SpecificationQualitySummary.SUMMARY_FOR_MUTATION_TESTING
        )
        or []
    )
    baseline_redundancy_summaries = _get_function_key_dict_from_list(
        baseline_spec_quality_type_to_records.get(
            SpecificationQualitySummary.SUMMARY_FOR_CLAUSE_REDUNDANCY
        )
        or []
    )
    experimental_redundancy_summaries = _get_function_key_dict_from_list(
        experimental_spec_quality_type_to_records.get(
            SpecificationQualitySummary.SUMMARY_FOR_CLAUSE_REDUNDANCY
        )
        or []
    )

    return {
        "mutation_summary": _get_baseline_experimental_function_diffs(
            baseline_mutation_testing_summaries,
            experimental_mutation_testing_summaries,
            _get_kill_score_comparison(
                baseline_mutation_testing_summaries, experimental_mutation_testing_summaries
            ),
            "kill_score_delta",
        ),
        "clause_redundancy_summary": _get_baseline_experimental_function_diffs(
            baseline_redundancy_summaries,
            experimental_redundancy_summaries,
            _get_redundancy_score_comparison(
                baseline_redundancy_summaries, experimental_redundancy_summaries
            ),
            "in_isolation_redundancy_rate_delta",
        ),
    }


def _format_score(value: float) -> str:
    """Return the score as a formatted string.

    Args:
        value (float): The score to format as a string.

    Returns:
        str: A score formatted as a string.
    """
    if isinstance(value, float):
        return f"{value:+.4f}"
    return f"{value:+d}"


def _format_section(title: str, section: dict[str, Any], delta_keys: list[str]) -> list[str]:
    lines = [title, "=" * len(title)]
    lines = [
        *lines,
        f"  baseline functions:  {section['baseline_count']}",
        f"  experimental functions: {section['experimental_count']}",
        f"  common:              {section['common_count']}",
    ]
    mean_key = next(k for k in section if k.startswith("mean_"))
    lines.append(f"  {mean_key}: {_format_score(section[mean_key])}")
    if section["only_in_baseline"]:
        lines.append(f"  only in baseline:  {', '.join(section['only_in_baseline'])}")
    if section["only_in_experimental"]:
        lines.append(f"  only in experimental: {', '.join(section['only_in_experimental'])}")
    if not section["per_function"]:
        lines.append("  (no common functions)")
        return lines

    headers = ["file#function", *delta_keys]
    rows = [headers]
    rows.extend(
        [f"{row['file']}#{row['function']}"] + [_format_score(row[k]) for k in delta_keys]
        for row in section["per_function"]
    )
    widths = [max(len(r[i]) for r in rows) for i in range(len(headers))]
    sep = "  " + "-+-".join("-" * w for w in widths)
    lines.append("")
    lines.append("  " + " | ".join(h.ljust(widths[i]) for i, h in enumerate(headers)))
    lines.append(sep)
    for row in rows[1:]:
        lines.append("  " + " | ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)))
    return lines


def _format_report(report: dict[str, Any]) -> str:
    lines = _format_section(
        "mutation_summary",
        report["mutation_summary"],
        ["kill_score_delta", "killed_delta", "total_delta"],
    )
    lines.append("")
    lines.extend(
        _format_section(
            "clause_redundancy_summary",
            report["clause_redundancy_summary"],
            [
                "in_isolation_redundancy_rate_delta",
                "caller_side_redundancy_rate_delta",
                "num_unobservable_delta",
                "num_unverifiable_baseline_delta",
            ],
        )
    )
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
