#!/usr/bin/env -S uv run --quiet python3

"""Compare two spec-quality JSONL logs and report per-function and aggregate deltas.

This script consumes the JSONL scripts produced by the `evaluate_specification_quality.py` script
for two experimental runs.

Usage:
    % ./eval/compare_specification_quality.py <BASELINE_JSONL> <experimental_JSONL> [--json]
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval.visualization.util import (
    FunctionKey,
    SpecificationQualitySummary,
    get_function_key,
    get_kill_scored_records,
    group_by_summary_type,
    yield_records,
)

_DEFAULT_NUM_DECIMAL_PLACES = 4
_FIELDS_FOR_KILL_SCORE_COMPARISON = {"kill_score", "killed", "total"}
_FIELDS_FOR_REDUNDANCY_RATE_COMPARISON = {
    "in_isolation_redundancy_rate",
    "caller_side_redundancy_rate",
    "num_unobservable",
    "num_unverifiable_baseline",
}


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


def _get_specification_quality_comparison(
    path_to_baseline_records: str, path_to_experimental_records: str
) -> dict[str, Any]:
    """Return the specification quality comparison for a baseline and experimental treatment.

    Args:
        path_to_baseline_records (str): Path to the baseline records.
        path_to_experimental_records (str): Path to the experimental records.

    Returns:
        dict[str, Any]: The specification quality comparison (comprising kill scores and redundancy
            scores) between a baseline and experimental treatment.
    """
    baseline_records = group_by_summary_type(yield_records(path_to_baseline_records))
    experimental_records = group_by_summary_type(yield_records(path_to_experimental_records))
    mutation = SpecificationQualitySummary.SUMMARY_FOR_MUTATION_TESTING
    redundancy = SpecificationQualitySummary.SUMMARY_FOR_CLAUSE_REDUNDANCY

    baseline_redundancy_summaries = _get_function_key_dict_from_list(baseline_records[redundancy])
    experimental_redundancy_summaries = _get_function_key_dict_from_list(
        experimental_records[redundancy]
    )
    return {
        "mutation_summary": _get_mutation_comparison(
            baseline_records[mutation], experimental_records[mutation]
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


def _get_mutation_comparison(
    baseline_records: list[dict], experimental_records: list[dict]
) -> dict[str, Any]:
    """Return the kill score comparison for the given baseline and experimental treatment.

    Only functions that have a kill score in both treatments are compared.  A function that has a
    kill score in one treatment and has a record without a kill score in the other (for example,
    because it did not verify) is listed separately from a function that has no record at all in
    the other treatment, and the functions without a kill score are counted, so that a treatment
    that breaks verification does not look better.

    Args:
        baseline_records (list[dict]): mutation summary records of the baseline, each a dict parsed
            from a JSONL line with keys such as "file", "function", and "kill_score".
        experimental_records (list[dict]): The mutation summary records of the experimental
            treatment, in the same format as `baseline_records`.

    Returns:
        dict[str, Any]: The kill score comparison between the baseline and experimental treatment.
            Its `only_in_baseline` lists the functions that have a kill score in the baseline and
            no record in the experimental treatment.  Its `unscored_in_experimental` lists the
            functions that have a kill score in the baseline and a record without a kill score in
            the experimental treatment.  `only_in_experimental` and `unscored_in_baseline` are
            symmetric.
    """
    baseline_recorded = _get_function_key_dict_from_list(baseline_records).keys()
    experimental_recorded = _get_function_key_dict_from_list(experimental_records).keys()
    baseline_scored = _get_function_key_dict_from_list(get_kill_scored_records(baseline_records))
    experimental_scored = _get_function_key_dict_from_list(
        get_kill_scored_records(experimental_records)
    )
    baseline_unscored = baseline_recorded - baseline_scored.keys()
    experimental_unscored = experimental_recorded - experimental_scored.keys()
    return {
        **_get_baseline_experimental_function_diffs(
            baseline_scored,
            experimental_scored,
            _get_kill_score_comparison(baseline_scored, experimental_scored),
            "kill_score_delta",
        ),
        "baseline_unscored_count": len(baseline_unscored),
        "experimental_unscored_count": len(experimental_unscored),
        "only_in_baseline": _format_function_keys(baseline_scored.keys() - experimental_recorded),
        "only_in_experimental": _format_function_keys(
            experimental_scored.keys() - baseline_recorded
        ),
        "unscored_in_baseline": _format_function_keys(
            experimental_scored.keys() & baseline_unscored
        ),
        "unscored_in_experimental": _format_function_keys(
            baseline_scored.keys() & experimental_unscored
        ),
    }


def _get_baseline_experimental_function_diffs(
    baseline_records: dict[FunctionKey, dict],
    experimental_records: dict[FunctionKey, dict],
    per_function: list[dict],
    headline_delta_key: str,
) -> dict[str, Any]:
    """Return the comparison of the given baseline and experimental treatment.

    A function might be scored in only one treatment.  For example, it might time out in one
    treatment and verify in the other.

    Args:
        baseline_records (dict[FunctionKey, dict]): The scored records of the baseline.
        experimental_records (dict[FunctionKey, dict]): The scored records of the experimental
            treatment.
        per_function (list[dict]): a comparison of the baseline and experimental records for each
            function that is scored in both treatments.
        headline_delta_key (str): The key in `per_function` whose mean is reported, such as
            "kill_score_delta".

    Returns:
        dict[str, Any]: The comparison, with counts, the functions scored in only one treatment,
            the mean of `headline_delta_key`, and `per_function`.
    """
    baseline_keys, experimental_keys = baseline_records.keys(), experimental_records.keys()
    deltas = [row[headline_delta_key] for row in per_function]
    return {
        "baseline_count": len(baseline_records),
        "experimental_count": len(experimental_records),
        "common_count": len(per_function),
        "only_in_baseline": _format_function_keys(baseline_keys - experimental_keys),
        "only_in_experimental": _format_function_keys(experimental_keys - baseline_keys),
        f"mean_{headline_delta_key}": round(statistics.fmean(deltas), 4) if deltas else 0.0,
        "per_function": per_function,
    }


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
        if not (
            _is_well_formed_record(baseline_record, _FIELDS_FOR_KILL_SCORE_COMPARISON)
            and _is_well_formed_record(experimental_record, _FIELDS_FOR_KILL_SCORE_COMPARISON)
        ):
            msg = (
                f"'{baseline_record}' and/or '{experimental_record}' is missing a field "
                f"from '{_FIELDS_FOR_KILL_SCORE_COMPARISON}'"
            )
            raise ValueError(msg)
        rows.append(
            {
                "file": file_name,
                "function": function,
                "baseline": baseline_record,
                "experimental": experimental_record,
                "kill_score_delta": round(
                    experimental_record["kill_score"] - baseline_record["kill_score"],
                    _DEFAULT_NUM_DECIMAL_PLACES,
                ),
                "killed_delta": experimental_record["killed"] - baseline_record["killed"],
                "total_delta": experimental_record["total"] - baseline_record["total"],
            }
        )
    rows.sort(key=lambda row: (-abs(row["kill_score_delta"]), row["file"], row["function"]))
    return rows


def _is_well_formed_record(record: dict, required_fields: set[str]) -> bool:
    """Return True iff all fields in the set of required fields appear in the record.

    Args:
        record (dict): The record to check for well-formedness.
        required_fields (set[str]): The set of fields to check for membership in the record.

    Returns:
        bool: True iff all fields in the set of required fields appear in the record.
    """
    return all(field in record for field in required_fields)


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
        if not (
            _is_well_formed_record(baseline_record, _FIELDS_FOR_REDUNDANCY_RATE_COMPARISON)
            and _is_well_formed_record(experimental_record, _FIELDS_FOR_REDUNDANCY_RATE_COMPARISON)
        ):
            msg = (
                f"'{baseline_record}' and/or '{experimental_record}' is missing a field "
                f"from '{_FIELDS_FOR_REDUNDANCY_RATE_COMPARISON}'"
            )
            raise ValueError(msg)
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
                "num_unobservable_delta": experimental_record["num_unobservable"]
                - baseline_record["num_unobservable"],
                "num_unverifiable_baseline_delta": experimental_record["num_unverifiable_baseline"]
                - baseline_record["num_unverifiable_baseline"],
            }
        )
    rows.sort(
        key=lambda row: (
            -abs(row["in_isolation_redundancy_rate_delta"]),
            row["file"],
            row["function"],
        )
    )
    return rows


def _get_function_key_dict_from_list(records: list[dict]) -> dict[FunctionKey, dict]:
    """Return a dictionary where records are indexed by `FunctionKey`.

    Args:
        records (list[dict]): The items from the JSONL file to index by `FunctionKey`.

    Returns:
        dict[FunctionKey, dict]: A dictionary where records are indexed by `FunctionKey`.
    """
    return {get_function_key(record): record for record in records}


def _format_function_keys(keys: Iterable[FunctionKey]) -> list[str]:
    """Return the given functions as sorted strings of the form "FILE#FUNCTION".

    Args:
        keys (Iterable[FunctionKey]): some functions.

    Returns:
        list[str]: The functions as sorted strings, such as "/benchmarks/q.c#f".
    """
    return sorted(f"{file_name}#{function}" for file_name, function in keys)


def _format_report(report_data: dict[str, Any]) -> str:
    """Return the formatted report data for a specification quality comparison.

    The formatted report comprises comparisons between a baseline and experimental treatment for
    specification generation in terms of a difference between kill scores and redundancy scores.

    Args:
        report_data (dict[str, Any]): The report data to format into a string.

    Returns:
        str: The formatted report data.
    """
    lines = _format_section(
        "mutation_summary",
        report_data["mutation_summary"],
        ["kill_score_delta", "killed_delta", "total_delta"],
    )
    lines.append("")
    lines.extend(
        _format_section(
            "clause_redundancy_summary",
            report_data["clause_redundancy_summary"],
            [
                "in_isolation_redundancy_rate_delta",
                "caller_side_redundancy_rate_delta",
                "num_unobservable_delta",
                "num_unverifiable_baseline_delta",
            ],
        )
    )
    return "\n".join(lines)


def _format_section(title: str, section_data: dict[str, Any], delta_keys: list[str]) -> list[str]:
    """Return the formatted section of a comparison report.

    Args:
        title (str): The title of a formatted section.
        section_data (dict[str, Any]): The data to display in the section.
        delta_keys (list[str]): The delta keys (e.g., for kill scores, redundancy scores).

    Returns:
        list[str]: The lines of a formatted section of a comparison report.
    """
    lines = [title, "=" * len(title)]
    lines = [
        *lines,
        f"  baseline scored functions:       {section_data['baseline_count']}",
        f"  experimental scored functions:   {section_data['experimental_count']}",
    ]
    if "baseline_unscored_count" in section_data:
        lines.extend(
            [
                f"  baseline unscored functions:     {section_data['baseline_unscored_count']}",
                f"  experimental unscored functions: {section_data['experimental_unscored_count']}",
            ]
        )
    lines.append(f"  common:                          {section_data['common_count']}")
    mean_key = next((k for k in section_data if k.startswith("mean_")), None)
    if not mean_key:
        msg = f"Expected a 'mean_*' key in {section_data} for {title}"
        raise ValueError(msg)

    lines.append(f"  {mean_key}: {_format_score(section_data[mean_key])}")
    if section_data["only_in_baseline"]:
        lines.append(f"  only in baseline:  {', '.join(section_data['only_in_baseline'])}")
    if section_data["only_in_experimental"]:
        lines.append(f"  only in experimental: {', '.join(section_data['only_in_experimental'])}")
    if section_data.get("unscored_in_baseline"):
        lines.append(
            f"  scored only in experimental (unscored in baseline): "
            f"{', '.join(section_data['unscored_in_baseline'])}"
        )
    if section_data.get("unscored_in_experimental"):
        lines.append(
            f"  scored only in baseline (unscored in experimental): "
            f"{', '.join(section_data['unscored_in_experimental'])}"
        )
    if not section_data["per_function"]:
        lines.append("  (no common functions)")
        return lines

    headers = ["file#function", *delta_keys]
    rows = [headers]
    rows.extend(
        [f"{row['file']}#{row['function']}"] + [_format_score(row[k]) for k in delta_keys]
        for row in section_data["per_function"]
    )
    widths = [max(len(r[i]) for r in rows) for i in range(len(headers))]
    sep = "  " + "-+-".join("-" * w for w in widths)
    lines.append("")
    lines.append("  " + " | ".join(h.ljust(widths[i]) for i, h in enumerate(headers)))
    lines.append(sep)
    for row in rows[1:]:
        lines.append("  " + " | ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)))
    return lines


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


if __name__ == "__main__":
    sys.exit(main())
