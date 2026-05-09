#!/usr/bin/env -S uv run --quiet python3

"""Top-level driver for spec-quality metrics.

Walks one or more annotated C files and emits a JSONL stream of metric records. Each metric
is a separate flag so the user can pay for only what they want — static metrics are cheap
and on by default; CBMC-driven metrics (mutation, clause redundancy, caller replaceability,
ground-truth comparison) each require a separate `--<metric>` flag.

Usage:
    eval/spec_quality.py <PATH_TO_C_FILE_OR_DIR>
        [--no-static]              # skip static AST metrics
        [--mutation]               # run M2.1 body-mutation kill rate
        [--redundancy]             # run M2.3 clause-removal redundancy
        [--replaceability]         # run M1.2 caller-side replaceability
        [--compare-against PATH]   # run M6.1+M3.4 against ground-truth file or directory
        [--jsonl PATH]             # write to file (default: stdout)

Records share a `kind` field so downstream tooling can demux them.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import IO

from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval.caller_replaceability import score_caller_replaceability
from eval.clause_redundancy import score_clause_redundancy
from eval.mutation_score import score_function_mutations
from eval.spec_compare import compare_preconditions, compare_via_verification
from tools.static_metrics import compute_static_metrics
from tools.util import get_functions_with_cprover_annotations


def main() -> int:
    """CLI entry point: orchestrate the spec-quality metric suite over a path.

    Returns:
        int: 0 on success, 1 when no `.c` files were found.
    """
    parser = argparse.ArgumentParser(
        description="Run spec-quality metrics over annotated C functions."
    )
    parser.add_argument("path", help="Path to a C file or directory of C files.")
    parser.add_argument(
        "--no-static",
        action="store_true",
        help="Skip static AST metrics (M2.2, M3.2, M3.3, M4.2, M4.3, M5.1).",
    )
    parser.add_argument(
        "--mutation",
        action="store_true",
        help="Run M2.1 body-mutation kill rate (CBMC-heavy).",
    )
    parser.add_argument(
        "--redundancy",
        action="store_true",
        help="Run M2.3 clause-removal redundancy (CBMC-heavy).",
    )
    parser.add_argument(
        "--replaceability",
        action="store_true",
        help="Run M1.2 caller-side replaceability (CBMC-heavy).",
    )
    parser.add_argument(
        "--compare-against",
        default=None,
        help=(
            "Run M6.1+M3.4 against this ground-truth file or directory. When a directory is "
            "given, files are matched by basename to the inputs under PATH."
        ),
    )
    parser.add_argument("--jsonl", default=None, help="Write records to this JSONL file.")
    args = parser.parse_args()

    logger.remove()
    logger.add(sys.stderr, level="INFO")

    inputs = _collect_c_files(args.path)
    if not inputs:
        logger.error(f"No .c files found at: {args.path}")
        return 1

    out: IO[str]
    if args.jsonl:
        out = Path(args.jsonl).open("w", encoding="utf-8")  # noqa: SIM115 — closed in finally
    else:
        out = sys.stdout
    try:
        for source in inputs:
            _process_file(
                source=source,
                out=out,
                run_static=not args.no_static,
                run_mutation=args.mutation,
                run_redundancy=args.redundancy,
                run_replaceability=args.replaceability,
                compare_root=Path(args.compare_against) if args.compare_against else None,
            )
    finally:
        if args.jsonl:
            out.close()
    return 0


def _process_file(
    source: Path,
    out: IO[str],
    run_static: bool,
    run_mutation: bool,
    run_redundancy: bool,
    run_replaceability: bool,
    compare_root: Path | None,
) -> None:
    """Run all enabled metrics for a single source file and write JSONL records to `out`."""
    logger.info(f"Processing {source}")
    if run_static:
        for metrics in compute_static_metrics(str(source)):
            out.write(json.dumps({"kind": "static_metrics", **asdict(metrics)}) + "\n")

    annotated = sorted(get_functions_with_cprover_annotations(str(source)))
    counterpart = _resolve_counterpart(source, compare_root) if compare_root else None
    if compare_root and counterpart is None:
        logger.warning(f"No ground-truth counterpart found for {source}; skipping comparison")

    for function in annotated:
        if run_mutation:
            score = score_function_mutations(str(source), function)
            out.write(
                json.dumps(
                    {
                        "kind": "mutation_summary",
                        "file": score.file,
                        "function": score.function,
                        "total": score.total,
                        "killed": score.killed,
                        "survived": score.survived,
                        "kill_rate": score.kill_rate,
                    }
                )
                + "\n"
            )

        if run_redundancy:
            score = score_clause_redundancy(str(source), function)
            out.write(
                json.dumps(
                    {
                        "kind": "clause_redundancy_summary",
                        "file": score.file,
                        "function": score.function,
                        "total_clauses": score.total_clauses,
                        "redundant": score.redundant,
                        "required": score.required,
                        "redundancy_rate": score.redundancy_rate,
                    }
                )
                + "\n"
            )

        if run_replaceability:
            score = score_caller_replaceability(str(source), function)
            out.write(
                json.dumps(
                    {
                        "kind": "caller_replaceability_summary",
                        "file": score.file,
                        "function": score.function,
                        "annotated_callers": score.annotated_callers,
                        "unannotated_callers": score.unannotated_callers,
                        "passed": score.passed,
                        "failed": score.failed,
                        "pass_rate": score.pass_rate,
                    }
                )
                + "\n"
            )

        if counterpart is not None:
            v = compare_via_verification(str(source), str(counterpart), function)
            out.write(json.dumps({"kind": "verification_comparison", **asdict(v)}) + "\n")
            p = compare_preconditions(str(source), str(counterpart), function)
            if p is None:
                out.write(
                    json.dumps(
                        {
                            "kind": "precondition_comparison",
                            "function": function,
                            "file_a": str(source),
                            "file_b": str(counterpart),
                            "skipped": "missing_requires",
                        }
                    )
                    + "\n"
                )
            else:
                out.write(json.dumps({"kind": "precondition_comparison", **asdict(p)}) + "\n")


def _collect_c_files(path_str: str) -> list[Path]:
    """Collect `.c` files from a path (file or directory). Sorted for determinism.

    Args:
        path_str (str): Path to a `.c` file or a directory containing `.c` files.

    Returns:
        list[Path]: Sorted list of `.c` file paths under the input.
    """
    path = Path(path_str)
    if not path.exists():
        return []
    if path.is_dir():
        return sorted(path.rglob("*.c"))
    return [path] if path.suffix == ".c" else []


def _resolve_counterpart(source: Path, compare_root: Path) -> Path | None:
    """Find the ground-truth file that corresponds to `source` under `compare_root`.

    If `compare_root` is a file, it is the counterpart. If it is a directory, search for
    a same-named `.c` file (e.g. `csv.c` matches `csv.c`).

    Args:
        source (Path): The source file whose ground-truth counterpart we want.
        compare_root (Path): The ground-truth file or directory.

    Returns:
        Path | None: The counterpart path, or None when no matching basename exists.
    """
    if compare_root.is_file():
        return compare_root
    matches = [p for p in compare_root.rglob(source.name) if p.is_file()]
    return matches[0] if matches else None


if __name__ == "__main__":
    sys.exit(main())
