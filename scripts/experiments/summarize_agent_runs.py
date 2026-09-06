#!/usr/bin/env -S uv run --quiet python3

"""Summarize agent time and cost from `avocado-verify` run logs.

Walks one or more run directories (as laid out by `run_agent_experiment.sh`), reads every
`<stem>-avocado-verify.jsonl` log beneath them, and reports per run:

- functions processed, verified, and the outcome histogram;
- agent time: the sum of claude-reported `duration_ms` across all sessions (wall-clock inside
  `claude -p`), plus the number of sessions and turns. A session that hit the harness's
  `--claude-timeout` reports no duration; it is counted as `--timeout-seconds` (default 1800, the
  harness default) and the number of such sessions is reported separately;
- total cost in USD as reported by claude. Timed-out sessions report no cost, so the cost of a run
  with timed-out sessions is a lower bound (also reported separately).

With several run directories, a final block reports the mean and spread of each aggregate.

Usage:
    % scripts/experiments/summarize_agent_runs.py <RUN_DIR> [<RUN_DIR> ...]
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from pathlib import Path


def main() -> None:
    """Print per-run agent-time/cost tables and cross-run aggregates."""
    parser = argparse.ArgumentParser(description="Summarize avocado-verify run logs.")
    parser.add_argument(
        "run_dirs", nargs="+", help="Run directories containing avocado-verify logs."
    )
    parser.add_argument("--quiet", action="store_true", help="Only print the aggregates.")
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=1800,
        help="Seconds to charge for a session that hit the harness timeout (default: 1800).",
    )
    args = parser.parse_args()

    aggregates = [
        summarize(Path(d), verbose=not args.quiet, timeout_seconds=args.timeout_seconds)
        for d in args.run_dirs
    ]
    if len(aggregates) > 1:
        print("\n== across runs ==")
        keys = (
            "functions",
            "verified",
            "sessions",
            "timed_out",
            "turns",
            "agent_seconds",
            "cost_usd",
        )
        for key in keys:
            values = [float(a[key]) for a in aggregates]
            spread = f"{min(values):.2f}..{max(values):.2f}"
            stdev = statistics.stdev(values) if len(values) > 1 else 0.0
            print(
                f"{key:14s} mean={statistics.fmean(values):.2f} spread={spread} stdev={stdev:.2f}"
            )


def summarize(
    run_dir: Path, *, verbose: bool, timeout_seconds: int = 1800
) -> dict[str, float | int]:
    """Print a summary of one run directory and return its aggregates.

    Args:
        run_dir (Path): Directory containing (possibly nested) `*-avocado-verify.jsonl` logs.
        verbose (bool): When True, print one line per function.
        timeout_seconds (int): Seconds charged for a session that hit the harness timeout.

    Returns:
        dict[str, float | int]: The aggregate metrics for the run.
    """
    outcomes: Counter[str] = Counter()
    sessions = turns = timed_out = 0
    agent_ms = 0
    cost = 0.0
    rows: list[str] = []
    for log in sorted(run_dir.rglob("*-avocado-verify.jsonl")):
        for line in log.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            if "type" in record:
                continue
            outcomes[record["outcome"]] += 1
            fn_timed_out = sum(1 for s in record["claude"] if s.get("timed_out"))
            fn_ms = sum(
                timeout_seconds * 1000 if s.get("timed_out") else (s.get("duration_ms") or 0)
                for s in record["claude"]
            )
            fn_turns = sum(s.get("num_turns") or 0 for s in record["claude"])
            fn_cost = float(record.get("total_cost_to_verify_usd") or 0)
            sessions += len(record["claude"])
            timed_out += fn_timed_out
            turns += fn_turns
            agent_ms += fn_ms
            cost += fn_cost
            flag = " (session timed out; cost unknown)" if fn_timed_out else ""
            rows.append(
                f"  {log.name.replace('-avocado-verify.jsonl', '')}#{record['function']:32s} "
                f"{record['outcome']:16s} sessions={len(record['claude'])} turns={fn_turns:3d} "
                f"attempts={record.get('verification_attempts', '?'):>2} "
                f"agent={fn_ms / 1000:7.1f}s cost=${fn_cost:6.2f}{flag}"
            )
    functions = sum(outcomes.values())
    aggregate: dict[str, float | int] = {
        "functions": functions,
        "verified": outcomes.get("VERIFIED", 0),
        "sessions": sessions,
        "timed_out": timed_out,
        "turns": turns,
        "agent_seconds": round(agent_ms / 1000, 1),
        "cost_usd": round(cost, 2),
    }
    print(f"== {run_dir}")
    if verbose:
        print("\n".join(rows))
    print(
        f"  functions={functions} outcomes={dict(outcomes)} sessions={sessions} "
        f"timed_out={timed_out} turns={turns} agent_time={agent_ms / 1000:.1f}s cost=${cost:.2f}"
        + (" (lower bound: timed-out sessions report no cost)" if timed_out else "")
    )
    return aggregate


if __name__ == "__main__":
    main()
