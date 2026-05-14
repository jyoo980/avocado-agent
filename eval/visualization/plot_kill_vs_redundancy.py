#!/usr/bin/env -S uv run --quiet python3

"""Scatter mutation kill score vs clause redundancy rate per function.

Joins `mutation_summary` and `clause_redundancy_summary` records on
(file, function) and plots the resulting points.

Usage:
    % ./eval/visualization/plot_kill_vs_redundancy.py <JSONL> \
        [--png PATH] \
        [--y {in_isolation,caller_side}]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parent))

from eval.visualization.util import (
    SpecificationQualitySummary,
    get_label_for_record,
    group_by_summary_type,
    yield_records,
)

Y_FIELDS = {
    "in_isolation": "in_isolation_redundancy_rate",
    "caller_side": "caller_side_redundancy_rate",
}


def main() -> None:
    """Generate a scatter plot of mutation score vs. clause redundancy rate per function."""
    parser = argparse.ArgumentParser(
        "Scatter plot of mutation score vs. clause redundancy rate per function."
    )
    parser.add_argument(
        "jsonl", help="Path to the JSONL produced by evaluate_specification_quality.py"
    )
    parser.add_argument("--png", default=None, help="Output a .png to this path.")
    parser.add_argument(
        "--y",
        choices=sorted(Y_FIELDS.keys()),
        default="in_isolation",
        help="Which redundancy rate to plot on the y-axis.",
    )
    args = parser.parse_args()

    grouped_records = group_by_summary_type(yield_records(args.jsonl))
    mutation_records = (
        grouped_records.get(SpecificationQualitySummary.SUMMARY_FOR_MUTATION_TESTING) or []
    )
    redundancy_records = (
        grouped_records.get(SpecificationQualitySummary.SUMMARY_FOR_CLAUSE_REDUNDANCY) or []
    )
    y_key = Y_FIELDS[args.y]
    points = _join(mutation_records, redundancy_records, y_key)
    y_label = f"{args.y}_redundancy_rate"

    if args.png:
        if not points:
            print("No joinable records; nothing to plot.", file=sys.stderr)
        _render_png(points, y_label, Path(args.png))
    else:
        _render_ascii(points, y_label)


def _join(
    mutation: list[dict], redundancy: list[dict], y_key: str
) -> list[tuple[str, float, float]]:
    by_key = {(r["file"], r["function"]): r for r in redundancy}
    points = []
    for record in mutation:
        match = by_key.get((record["file"], record["function"]))
        if match is None:
            continue
        points.append((get_label_for_record(record), record["kill_score"], match[y_key]))
    return points


def _render_ascii(
    points: list[tuple[str, float, float]],
    y_label: str,
) -> None:
    if not points:
        print("No joinable (file, function) pairs found.")
        return
    width, height = 60, 20
    grid = [[" "] * width for _ in range(height)]
    for _, x, y in points:
        col = min(width - 1, max(0, round(x * (width - 1))))
        row = min(height - 1, max(0, round((1 - y) * (height - 1))))
        grid[row][col] = "*" if grid[row][col] == " " else "#"
    print(f"kill_score (x) vs {y_label} (y), n={len(points)}")
    print("1.0 +" + "-" * width + "+")
    for row_idx, row in enumerate(grid):
        marker = "0.5" if row_idx == height // 2 else "   "
        print(f"{marker} |" + "".join(row) + "|")
    print("0.0 +" + "-" * width + "+")
    print("    0.0" + " " * (width - 6) + "1.0")


def _render_png(
    points: list[tuple[str, float, float]],
    y_label: str,
    png_path: Path,
) -> None:

    xs = [p[1] for p in points]
    ys = [p[2] for p in points]
    fig, ax = plt.subplots(figsize=(8, 6))
    ax.scatter(xs, ys, color="#4c72b0", alpha=0.75)
    ax.set_xlim(-0.02, 1.02)
    ax.set_ylim(-0.02, 1.02)
    ax.set_xlabel("Mutation kill score")
    ax.set_ylabel(y_label)
    ax.set_title(f"kill_score vs {y_label}  (n={len(points)})")

    if xs:
        sorted_x = sorted(xs)
        sorted_y = sorted(ys)
        low_x, high_x = sorted_x[len(sorted_x) // 10], sorted_x[-1 - len(sorted_x) // 10]
        low_y, high_y = sorted_y[len(sorted_y) // 10], sorted_y[-1 - len(sorted_y) // 10]
        for label, x, y in points:
            if x <= low_x or x >= high_x or y <= low_y or y >= high_y:
                ax.annotate(label, (x, y), fontsize=7, xytext=(3, 3), textcoords="offset points")

    fig.tight_layout()
    fig.savefig(png_path, dpi=150)
    plt.close(fig)
    print(f"Wrote {png_path}")


if __name__ == "__main__":
    sys.exit(main())
