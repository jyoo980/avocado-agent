#!/usr/bin/env -S uv run --quiet python3

"""Compare two arms of a paired agent run over only the functions both arms specified.

`compare_arms.py` pools every function each arm produced, which is the right comparison when both
arms finished. When a run is cut short -- an account usage limit is the usual cause -- the arms
stop at different functions, and pooling then rewards whichever arm happened to stop earlier or on
easier functions. This script restricts both arms to the intersection of the functions that appear
in both score files, so the remaining comparison is like-for-like.

A function that carries a contract which does not verify contributes a kill score of 0, exactly as
in `compare_arms.py`; a function with no mutants is excluded from both arms.

Usage:
    % scripts/experiments/compare_on_intersection.py <BASELINE_JSONL> <TREATMENT_JSONL>
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path


def main() -> None:
    """Print the intersection-restricted comparison of two score files."""
    parser = argparse.ArgumentParser(description="Compare two arms over their common functions.")
    parser.add_argument("baseline", help="Baseline JSONL from evaluate_specification_quality.")
    parser.add_argument("treatment", help="Treatment JSONL from evaluate_specification_quality.")
    args = parser.parse_args()

    baseline = _records(Path(args.baseline))
    treatment = _records(Path(args.treatment))
    common = sorted(baseline.keys() & treatment.keys())
    print(
        f"baseline functions: {len(baseline)}  treatment functions: {len(treatment)}  "
        f"common: {len(common)}"
    )
    only_baseline = sorted(baseline.keys() - treatment.keys())
    only_treatment = sorted(treatment.keys() - baseline.keys())
    if only_baseline:
        print(f"  only in baseline: {', '.join(only_baseline)}")
    if only_treatment:
        print(f"  only in treatment: {', '.join(only_treatment)}")

    scored: list[tuple[str, tuple[float, int, int], tuple[float, int, int]]] = []
    for name in common:
        baseline_score, baseline_killed, baseline_decided = baseline[name]
        treatment_score, treatment_killed, treatment_decided = treatment[name]
        if baseline_score is None or treatment_score is None:
            continue
        scored.append(
            (
                name,
                (baseline_score, baseline_killed, baseline_decided),
                (treatment_score, treatment_killed, treatment_decided),
            )
        )
    print(f"  scorable in both (has mutants in both): {len(scored)}")
    if not scored:
        return
    baseline_scores = [b[0] for _, b, _ in scored]
    treatment_scores = [t[0] for _, _, t in scored]
    baseline_killed = sum(b[1] for _, b, _ in scored)
    baseline_decided = sum(b[2] for _, b, _ in scored)
    treatment_killed = sum(t[1] for _, _, t in scored)
    treatment_decided = sum(t[2] for _, _, t in scored)
    print(
        f"  mean kill score:   baseline {statistics.fmean(baseline_scores):.4f}  "
        f"treatment {statistics.fmean(treatment_scores):.4f}  "
        f"delta {statistics.fmean(treatment_scores) - statistics.fmean(baseline_scores):+.4f}"
    )
    print(
        f"  pooled kill score: baseline {baseline_killed}/{baseline_decided} = "
        f"{baseline_killed / baseline_decided if baseline_decided else 0:.4f}  "
        f"treatment {treatment_killed}/{treatment_decided} = "
        f"{treatment_killed / treatment_decided if treatment_decided else 0:.4f}"
    )
    print("  per-function (baseline -> treatment), differing only:")
    for name, b, t in scored:
        if abs(b[0] - t[0]) > 1e-9:
            print(f"    {name:32s} {b[0]:.4f} ({b[1]}/{b[2]}) -> {t[0]:.4f} ({t[1]}/{t[2]})")


def _records(path: Path) -> dict[str, tuple[float | None, int, int]]:
    """Return each function's (kill score, killed, decided) from a score file.

    A function whose contract did not verify scores 0 over 0 decided mutants; a function with no
    mutants has `None` as its score so callers can exclude it.

    Args:
        path (Path): The JSONL file to read.

    Returns:
        dict[str, tuple[float | None, int, int]]: Per-function score, killed count, decided count.
    """
    out: dict[str, tuple[float | None, int, int]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("kind") != "mutation_summary":
            continue
        name = record["function"]
        if record.get("was_mutation_tested"):
            killed = int(record["killed"])
            decided = killed + int(record["survived"])
            out[name] = (float(record["kill_score"]), killed, decided)
        elif "did not verify" in record.get("metadata", ""):
            out[name] = (0.0, 0, 0)
        else:
            out[name] = (None, 0, 0)
    return out


if __name__ == "__main__":
    main()
