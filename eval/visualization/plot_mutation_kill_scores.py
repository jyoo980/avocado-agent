#!/usr/bin/env -S uv run --quiet python3

"""Plot per-function mutation kill scores from an evaluate_specification_quality.py JSONL stream.

Reads `mutation_summary` records and renders either an ASCII bar chart to
stdout or a matplotlib horizontal bar chart to a PNG.

Usage:
    % ./eval/visualization/plot_mutation_kill_scores.py <JSONL> [--png PATH]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parent))

from eval.visualization.util import (
    SpecificationQualitySummary,
    ascii_hbar,
    get_label_for_record,
    get_terminal_width,
    group_by_summary_type,
    yield_records,
)


def main() -> None:
    """Plot per-function kill scores from an evaluate_specification_quality.py JSONL stream."""
    description = (
        "Plot per-function kill scores from an evaluate_specification_quality.py JSONL stream."
    )
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "jsonl", help="Path to the JSONL produced by evaluate_specification_quality.py"
    )
    parser.add_argument("--png", default=None, help="Render to this PNG path instead of ASCII.")
    args = parser.parse_args()

    grouped_records = group_by_summary_type(yield_records(args.jsonl))
    mutation_records = (
        grouped_records.get(SpecificationQualitySummary.SUMMARY_FOR_MUTATION_TESTING) or []
    )
    records = _sort_records_by_kill_score(mutation_records)

    if not records:
        print("No mutation_summary records found; nothing to plot.", file=sys.stderr)

    _sort_records_by_kill_score(records)
    if args.png:
        _render_png(records, Path(args.png))
    else:
        _render_ascii(records)


def _sort_records_by_kill_score(records: list[dict]) -> list[dict]:
    return sorted(records, key=lambda r: (r["kill_score"], -r["total"]))


def _render_ascii(records: list[dict]) -> None:
    if not records:
        print("No mutation_summary records found.")
        return
    labels = [get_label_for_record(r) for r in records]
    label_width = min(max(len(label) for label in labels), 60)
    annot_width = 18  # "  10/12  score=0.833"
    bar_width = max(10, get_terminal_width() - label_width - annot_width - 2)
    print(f"Mutation kill scores ({len(records)} functions, worst first)")
    print("=" * (label_width + annot_width + bar_width + 2))
    for record, label in zip(records, labels, strict=True):
        bar = ascii_hbar(record["kill_score"], bar_width)
        annot = f"{record['killed']:>4}/{record['total']:<4} {record['kill_score']:.3f}"
        print(f"{label[:label_width].ljust(label_width)} {bar} {annot}")


def _render_png(records: list[dict], png_path: Path) -> None:

    labels = [get_label_for_record(r) for r in records]
    scores = [r["kill_score"] for r in records]
    height = max(2.5, 0.3 * len(records) + 1.5)
    fig, ax = plt.subplots(figsize=(10, height))
    ax.barh(labels, scores, color="#4c72b0")
    ax.set_xlim(0.0, 1.0)
    ax.set_xlabel("Kill score")
    ax.set_title(f"Mutation kill scores ({len(records)} functions)")
    ax.invert_yaxis()
    for i, record in enumerate(records):
        ax.text(
            min(scores[i] + 0.01, 0.99),
            i,
            f"{record['killed']}/{record['total']}",
            va="center",
            fontsize=8,
        )
    fig.tight_layout()
    fig.savefig(png_path, dpi=150)
    plt.close(fig)
    print(f"Wrote {png_path}")


if __name__ == "__main__":
    sys.exit(main())
