#!/usr/bin/env -S uv run --quiet python3

"""Compare two arms of a paired agent experiment.

Given a baseline label and a treatment label plus the run ids they share, reports for each arm:

- the mean and spread of the per-run mean kill score and pooled kill score, over the benchmarks
  named on the command line;
- how many functions each run verified, and how many were left unverified or unspecified;
- agent time (seconds inside `claude -p`) and cost. A session killed by the harness's
  `--claude-timeout` reports neither duration nor cost, so it is charged `--timeout-seconds`
  (default 1800, the harness default) and counted in `timed_out`; the arm's cost is then a lower
  bound.

A run's benchmarks are read from `<data-dir>/<label>-<run-id>-<benchmark>.jsonl` (scores) and
`<data-dir>/runs/<label>/<run-id>/` (agent logs), the layout `run_agent_experiment.sh` produces.

Because the arms are run in paired batches (one run of each arm at a time, on the same machine),
the per-batch difference is the meaningful quantity; the report prints it alongside the arm means.

Usage:
    % scripts/experiments/compare_arms.py --baseline base --treatment final --runs 4 5 6 \
          --benchmarks quicksort csv_parser [--data-dir avocado-experimental-data]
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path


def main() -> None:
    """Print the paired comparison of two experiment arms."""
    parser = argparse.ArgumentParser(description="Compare two arms of a paired agent experiment.")
    parser.add_argument("--baseline", required=True, help="Label of the baseline arm.")
    parser.add_argument("--treatment", required=True, help="Label of the treatment arm.")
    parser.add_argument("--runs", nargs="+", required=True, help="Run ids shared by both arms.")
    parser.add_argument("--benchmarks", nargs="+", required=True, help="Benchmark names to pool.")
    parser.add_argument(
        "--data-dir", default="avocado-experimental-data", help="Directory holding the results."
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=1800,
        help="Seconds charged for a session the harness killed on timeout (default: 1800).",
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    rows: dict[str, list[dict[str, float]]] = {}
    for label in (args.baseline, args.treatment):
        rows[label] = [
            _run_metrics(data_dir, label, run_id, args.benchmarks, args.timeout_seconds)
            for run_id in args.runs
        ]

    keys = (
        "mean_kill_score",
        "pooled_kill_score",
        "killed",
        "decided",
        "tested",
        "unscored",
        "verified",
        "functions",
        "timed_out_sessions",
        "agent_seconds",
        "cost_usd",
    )
    print(f"{'metric':18s} {args.baseline:>26s} {args.treatment:>26s} {'delta':>10s}")
    for key in keys:
        baseline_values = [row[key] for row in rows[args.baseline]]
        treatment_values = [row[key] for row in rows[args.treatment]]
        baseline_mean = statistics.fmean(baseline_values)
        treatment_mean = statistics.fmean(treatment_values)
        print(
            f"{key:18s} "
            f"{_cell(baseline_values, baseline_mean):>26s} "
            f"{_cell(treatment_values, treatment_mean):>26s} "
            f"{treatment_mean - baseline_mean:+10.3f}"
        )
    print("\nper-batch deltas (treatment - baseline, paired by run id):")
    for index, run_id in enumerate(args.runs):
        baseline_row = rows[args.baseline][index]
        treatment_row = rows[args.treatment][index]
        delta = {key: treatment_row[key] - baseline_row[key] for key in treatment_row}
        print(
            f"  run {run_id}: mean_kill_score {delta['mean_kill_score']:+.4f}"
            f"  pooled {delta['pooled_kill_score']:+.4f}"
            f"  verified {delta['verified']:+.0f}"
            f"  agent_s {delta['agent_seconds']:+.0f}"
            f"  usd {delta['cost_usd']:+.2f}"
        )


def _cell(values: list[float], mean: float) -> str:
    """Return a "mean [min..max]" cell for a metric's values across runs.

    Args:
        values (list[float]): One value per run.
        mean (float): The mean of those values.

    Returns:
        str: The formatted cell.
    """
    return f"{mean:8.3f} [{min(values):.3f}..{max(values):.3f}]"


def _run_metrics(
    data_dir: Path, label: str, run_id: str, benchmarks: list[str], timeout_seconds: int
) -> dict[str, float]:
    """Return one run's pooled quality metrics and agent cost.

    Quality is pooled over every benchmark named, so an arm cannot look better by specifying fewer
    functions: `unscored` counts functions that were annotated but not mutation tested (they did
    not verify), and `functions`/`verified` come from the harness's own run log.

    Args:
        data_dir (Path): Directory holding `<label>-<run-id>-<benchmark>.jsonl` and `runs/`.
        label (str): The arm's label.
        run_id (str): The run id.
        benchmarks (list[str]): Benchmark names to pool.
        timeout_seconds (int): Seconds charged for a session the harness killed on timeout, which
            reports no duration of its own.

    Returns:
        dict[str, float]: The run's metrics.
    """
    scores: list[float] = []
    killed = decided = tested = unscored = 0
    for benchmark in benchmarks:
        path = data_dir / f"{label}-{run_id}-{benchmark}.jsonl"
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get("kind") != "mutation_summary":
                continue
            if record.get("was_mutation_tested"):
                scores.append(float(record["kill_score"]))
                killed += int(record["killed"])
                decided += int(record["killed"]) + int(record["survived"])
                tested += 1
            elif "did not verify" in record.get("metadata", ""):
                # A function with a spec that does not verify scores nothing; count it so an arm
                # that leaves specs unverifiable does not look better than one that does not.
                scores.append(0.0)
                unscored += 1

    functions = verified = timed_out_sessions = 0
    agent_ms = 0.0
    cost = 0.0
    run_dir = data_dir / "runs" / label / run_id
    for log in sorted(run_dir.rglob("*-avocado-verify.jsonl")):
        for line in log.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            if "type" in record:
                continue
            functions += 1
            verified += record["outcome"] == "VERIFIED"
            timed_out_sessions += sum(1 for s in record["claude"] if s.get("timed_out"))
            agent_ms += sum(
                timeout_seconds * 1000 if s.get("timed_out") else (s.get("duration_ms") or 0)
                for s in record["claude"]
            )
            cost += float(record.get("total_cost_to_verify_usd") or 0)

    return {
        "mean_kill_score": statistics.fmean(scores) if scores else 0.0,
        "pooled_kill_score": killed / decided if decided else 0.0,
        "killed": float(killed),
        "decided": float(decided),
        "tested": float(tested),
        "unscored": float(unscored),
        "verified": float(verified),
        "functions": float(functions),
        "timed_out_sessions": float(timed_out_sessions),
        "agent_seconds": agent_ms / 1000,
        "cost_usd": cost,
    }


if __name__ == "__main__":
    main()
