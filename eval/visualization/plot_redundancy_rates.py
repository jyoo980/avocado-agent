#!/usr/bin/env -S uv run --quiet python3

"""Plot per-function clause redundancy rates (in-isolation vs caller-side).

Reads `clause_redundancy_summary` records from an
`evaluate_specification_quality.py` JSONL stream and renders grouped bars
either as ASCII or to a PNG via matplotlib.

Flags appended to each label:
    [U] -> num_unobservable > 0     (some clauses have no in-file callers)
    [B] -> num_unverifiable_baseline > 0  (some callers fail baseline verification)

Usage:
    % ./eval/visualization/plot_redundancy_rates.py <JSONL> [--png PATH]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import matplotlib.pyplot as plt
import numpy as np

from eval.visualization.util import (
    SpecificationQualitySummary,
    ascii_hbar,
    get_label_for_record,
    get_terminal_width,
    group_by_summary_type,
    yield_records,
)


def main() -> None:
    """Plot per-function clause redundancy rates (in-isolation vs caller-side)."""
    parser = argparse.ArgumentParser(
        "Plot per-function clause redundancy rates (in-isolation vs caller-side)."
    )
    parser.add_argument(
        "jsonl", help="Path to the JSONL produced by evaluate_specification_quality.py"
    )
    parser.add_argument("--png", default=None, help="Render to this PNG path instead of ASCII.")
    args = parser.parse_args()

    record_summary_groups = group_by_summary_type(yield_records(args.jsonl))
    records = (
        record_summary_groups.get(SpecificationQualitySummary.SUMMARY_FOR_CLAUSE_REDUNDANCY) or []
    )

    if not records:
        print("No clause_redundancy_summary records found; nothing to plot.", file=sys.stderr)
        return

    records = _sort_records(records)
    if args.png:
        _render_png(records, Path(args.png))
    else:
        _render_ascii(records)


def _flags(record: dict) -> str:
    parts = []
    if record.get("num_unobservable", 0) > 0:
        parts.append("U")
    if record.get("num_unverifiable_baseline", 0) > 0:
        parts.append("B")
    return f" [{''.join(parts)}]" if parts else ""


def _sort_records(records: list[dict]) -> list[dict]:
    return sorted(
        records,
        key=lambda r: -max(r["in_isolation_redundancy_rate"], r["caller_side_redundancy_rate"]),
    )


def _render_ascii(records: list[dict]) -> None:
    labels = [get_label_for_record(r) + _flags(r) for r in records]
    label_width = min(max(len(label) for label in labels), 60)
    annot_width = 8
    bar_width = max(10, get_terminal_width() - label_width - annot_width - 6)
    print(f"Clause redundancy ({len(records)} functions; U=unobservable, B=unverifiable baseline)")
    print("=" * (label_width + annot_width + bar_width + 6))
    for record, label in zip(records, labels, strict=True):
        iso = record["in_isolation_redundancy_rate"]
        cal = record["caller_side_redundancy_rate"]
        print(
            f"{label[:label_width].ljust(label_width)} iso {ascii_hbar(iso, bar_width)} {iso:.3f}"
        )
        print(f"{' ' * label_width} cal {ascii_hbar(cal, bar_width)} {cal:.3f}")


def _render_png(records: list[dict], png_path: Path) -> None:
    labels = [get_label_for_record(r) + _flags(r) for r in records]
    iso = [r["in_isolation_redundancy_rate"] for r in records]
    cal = [r["caller_side_redundancy_rate"] for r in records]
    indices = np.arange(len(records))
    bar_height = 0.4
    height = max(3.0, 0.45 * len(records) + 1.5)
    fig, ax = plt.subplots(figsize=(10, height))
    ax.barh(indices - bar_height / 2, iso, bar_height, label="in-isolation", color="#4c72b0")
    ax.barh(indices + bar_height / 2, cal, bar_height, label="caller-side", color="#dd8452")
    ax.set_yticks(indices)
    ax.set_yticklabels(labels)
    ax.invert_yaxis()
    ax.set_xlim(0.0, 1.0)
    ax.set_xlabel("Redundancy rate")
    ax.set_title(f"Clause redundancy ({len(records)} functions)")
    ax.legend(loc="lower right")
    fig.tight_layout()
    fig.savefig(png_path, dpi=150)
    plt.close(fig)
    print(f"Wrote {png_path}")


if __name__ == "__main__":
    sys.exit(main())
