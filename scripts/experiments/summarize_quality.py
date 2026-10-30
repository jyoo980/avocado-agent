#!/usr/bin/env -S uv run --quiet python3

"""Summarize one or more `evaluate_specification_quality.py --mutation` JSONL files.

For each JSONL file, prints the per-function kill scores and two aggregates:

- `mean_kill_score`: the arithmetic mean of `kill_score` over mutation-tested functions (what
  `eval/compare_specification_quality.py` reports as `mean_kill_score_delta` when differenced).
- `pooled_kill_score`: total killed / total decided mutants across all mutation-tested functions.

Functions that were not mutation tested (did not verify, or had no mutants) are listed separately
so a treatment cannot look better merely by verifying fewer functions. When several JSONL files are
given (e.g. three independent agent runs), a final block reports the mean and spread (min..max)
of both aggregates across files, which is the noise floor for agent-time experiments.

Usage:
    % scripts/experiments/summarize_quality.py <JSONL> [<JSONL> ...] [--quiet]
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path


def main() -> None:
    """Print per-file kill-score tables and cross-file aggregates."""
    parser = argparse.ArgumentParser(description="Summarize spec-quality JSONL files.")
    parser.add_argument(
        "jsonl", nargs="+", help="JSONL file(s) from evaluate_specification_quality."
    )
    parser.add_argument("--quiet", action="store_true", help="Only print the aggregates.")
    args = parser.parse_args()

    aggregates: list[dict[str, float | int]] = []
    for path in args.jsonl:
        aggregate = summarize(Path(path), verbose=not args.quiet)
        aggregates.append(aggregate)
    if len(aggregates) > 1:
        print("\n== across files ==")
        for key in (
            "mean_kill_score",
            "pooled_kill_score",
            "killed",
            "decided",
            "tested",
            "untested",
        ):
            values = [float(a[key]) for a in aggregates]
            spread = f"{min(values):.4f}..{max(values):.4f}"
            stdev = statistics.stdev(values) if len(values) > 1 else 0.0
            print(
                f"{key:18s} mean={statistics.fmean(values):.4f} spread={spread} stdev={stdev:.4f}"
            )


def summarize(path: Path, *, verbose: bool) -> dict[str, float | int]:
    """Print a summary of one JSONL file and return its aggregates.

    Args:
        path (Path): The JSONL file to summarize.
        verbose (bool): When True, print one line per function.

    Returns:
        dict[str, float | int]: The aggregate metrics for the file.
    """
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    records = [r for r in records if r.get("kind") == "mutation_summary"]
    tested = [r for r in records if r.get("was_mutation_tested")]
    untested = [r for r in records if not r.get("was_mutation_tested")]
    scores = [float(r["kill_score"]) for r in tested]
    killed = sum(int(r["killed"]) for r in tested)
    decided = sum(int(r["killed"]) + int(r["survived"]) for r in tested)
    aggregate: dict[str, float | int] = {
        "mean_kill_score": round(statistics.fmean(scores), 4) if scores else 0.0,
        "pooled_kill_score": round(killed / decided, 4) if decided else 0.0,
        "killed": killed,
        "decided": decided,
        "tested": len(tested),
        "untested": len(untested),
    }
    print(f"== {path}")
    if verbose:
        for r in tested:
            print(
                f"  {Path(r['file']).name}#{r['function']:35s} "
                f"{int(r['killed']):3d}/{int(r['killed']) + int(r['survived']):3d} decided "
                f"(timed_out={r['timed_out']} compile_failed={r['compile_failed']}) "
                f"kill_score={float(r['kill_score']):.4f}"
            )
        for r in untested:
            reason = "did not verify" if "did not verify" in r.get("metadata", "") else "no mutants"
            print(f"  {Path(r['file']).name}#{r['function']:35s} -- {reason}")
    print(
        f"  mean_kill_score={aggregate['mean_kill_score']:.4f} "
        f"pooled_kill_score={aggregate['pooled_kill_score']:.4f} "
        f"({killed}/{decided} decided mutants; {len(tested)} tested, {len(untested)} untested)"
    )
    return aggregate


if __name__ == "__main__":
    main()
