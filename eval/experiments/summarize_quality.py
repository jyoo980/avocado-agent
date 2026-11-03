#!/usr/bin/env -S uv run --quiet python3

"""Summarize one or more `evaluate_specification_quality.py --mutation` JSONL files.

For each JSONL file, prints the per-function kill scores and two aggregates:

- `mean_kill_score`: the arithmetic mean of `kill_score` over mutation-tested functions that have
  at least one decided mutant.
- `pooled_kill_score`: total killed / total decided mutants across all mutation-tested functions.

A file in which no function has a decided mutant has neither aggregate:  the script prints "n/a"
for them.

Functions that were not mutation tested (did not verify, or had no mutants) are listed separately
so a treatment cannot look better merely by verifying fewer functions.  So are functions whose
mutants were all undecided (timed out, failed to compile, or failed instrumentation):  their
`kill_score` of 0.0 does not mean that a mutant survived, so it is not part of `mean_kill_score`.

When several JSONL files are given (e.g. three independent agent runs), a final block reports the
mean, spread (min..max), and standard deviation of both aggregates and of the counts across files,
which is the noise floor for agent-time experiments.  A file without the aggregates is left out of
their statistics in that block.  A file with no mutation summary records, such as the output of a
failed run, is reported, left out of that block, and makes the script exit with a non-zero status.
The script rejects a file whose name ends in `.failed`, which `measure_quality.sh` gives to the
JSONL file of a failed or interrupted run.

Usage:
    % eval/experiments/summarize_quality.py <JSONL> [<JSONL> ...] [--quiet]
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from eval.visualization.util import (
    SpecificationQualitySummary,
    group_by_summary_type,
    yield_records,
)


def main() -> None:
    """Print per-file kill-score tables and cross-file aggregates."""
    parser = argparse.ArgumentParser(description="Summarize spec-quality JSONL files.")
    parser.add_argument(
        "jsonl", nargs="+", help="JSONL file(s) from evaluate_specification_quality."
    )
    parser.add_argument("--quiet", action="store_true", help="Only print the aggregates.")
    args = parser.parse_args()
    # `measure_quality.sh` renames the JSONL file of a failed or interrupted run to end in
    # `.failed`.  Such a file can hold valid records for the functions that finished, which would
    # skew the aggregates.
    failed_runs = [path for path in args.jsonl if path.endswith(".failed")]
    if failed_runs:
        parser.error(f"output of a failed run: {', '.join(failed_runs)}")

    aggregates: list[dict[str, float | int | None]] = []
    files_without_records: list[str] = []
    for path in args.jsonl:
        aggregate = summarize(Path(path), verbose=not args.quiet)
        if aggregate is None:
            files_without_records.append(path)
        else:
            aggregates.append(aggregate)
    if len(aggregates) > 1:
        print("\n== across files ==")
        for key in (
            "mean_kill_score",
            "pooled_kill_score",
            "killed",
            "decided",
            "tested",
            "undecided",
            "untested",
        ):
            values = [float(value) for a in aggregates if (value := a[key]) is not None]
            if not values:
                print(f"{key:18s} n/a")
                continue
            spread = f"{min(values):.4f}..{max(values):.4f}"
            stdev = f"{statistics.stdev(values):.4f}" if len(values) > 1 else "n/a"
            omitted = (
                f" ({len(values)} of {len(aggregates)} files)"
                if len(values) < len(aggregates)
                else ""
            )
            print(
                f"{key:18s} mean={statistics.fmean(values):.4f} spread={spread} stdev={stdev}"
                f"{omitted}"
            )
    if files_without_records:
        print(
            f"error: no mutation summary records in: {', '.join(files_without_records)}",
            file=sys.stderr,
        )
        sys.exit(1)


def summarize(path: Path, *, verbose: bool) -> dict[str, float | int | None] | None:
    """Print a summary of the given JSONL file and return its aggregates.

    Args:
        path (Path): a JSONL file that `evaluate_specification_quality.py --mutation` wrote.
        verbose (bool): True if the summary should include one line per function.

    Returns:
        dict[str, float | int | None] | None: The aggregate metrics for the file, with keys
            "mean_kill_score", "pooled_kill_score", "killed", "decided", "tested", "undecided",
            and "untested", or None if the file has no mutation summary records.  The values of
            "mean_kill_score" and "pooled_kill_score" are None if no function has a decided mutant.
    """
    records = group_by_summary_type(yield_records(str(path))).get(
        SpecificationQualitySummary.SUMMARY_FOR_MUTATION_TESTING, []
    )
    print(f"== {path}")
    if not records:
        print("  no mutation summary records")
        return None
    tested = [r for r in records if r.get("was_mutation_tested") and _num_decided(r) > 0]
    undecided = [r for r in records if r.get("was_mutation_tested") and _num_decided(r) == 0]
    untested = [r for r in records if not r.get("was_mutation_tested")]
    scores = [float(r["kill_score"]) for r in tested]
    killed = sum(int(r["killed"]) for r in tested)
    decided = sum(_num_decided(r) for r in tested)
    aggregate: dict[str, float | int | None] = {
        "mean_kill_score": statistics.fmean(scores) if scores else None,
        "pooled_kill_score": killed / decided if decided else None,
        "killed": killed,
        "decided": decided,
        "tested": len(tested),
        "undecided": len(undecided),
        "untested": len(untested),
    }
    if verbose:
        for r in tested:
            print(
                f"  {Path(r['file']).name}#{r['function']:35s} "
                f"{int(r['killed']):3d}/{_num_decided(r):3d} decided "
                f"(timed_out={r['timed_out']} compile_failed={r['compile_failed']}) "
                f"kill_score={float(r['kill_score']):.4f}"
            )
        for r in undecided:
            print(
                f"  {Path(r['file']).name}#{r['function']:35s} -- no decided mutants "
                f"(timed_out={r['timed_out']} compile_failed={r['compile_failed']} "
                f"instrumentation_failed={r['instrumentation_failed']})"
            )
        for r in untested:
            reason = "did not verify" if "did not verify" in r.get("metadata", "") else "no mutants"
            print(f"  {Path(r['file']).name}#{r['function']:35s} -- {reason}")
    print(
        f"  mean_kill_score={_format_score(aggregate['mean_kill_score'])} "
        f"pooled_kill_score={_format_score(aggregate['pooled_kill_score'])} "
        f"({killed}/{decided} decided mutants; {len(tested)} tested, "
        f"{len(undecided)} with no decided mutants, {len(untested)} untested)"
    )
    return aggregate


def _format_score(score: float | None) -> str:
    """Return the given kill score with 4 decimal places, or "n/a" if it is None.

    Args:
        score (float | None): a kill score such as 0.75, or None if there is none.

    Returns:
        str: The formatted kill score, such as "0.7500".
    """
    return "n/a" if score is None else f"{score:.4f}"


def _num_decided(record: dict) -> int:
    """Return the number of decided (killed or survived) mutants in the given record.

    Args:
        record (dict): a mutation-tested function's `mutation_summary` record, as a dict parsed
            from the JSONL, with keys such as "killed", "survived", and "kill_score".

    Returns:
        int: The number of decided mutants.
    """
    return int(record["killed"]) + int(record["survived"])


if __name__ == "__main__":
    main()
