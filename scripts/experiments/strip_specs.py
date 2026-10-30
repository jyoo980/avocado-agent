#!/usr/bin/env -S uv run --quiet python3

"""Copy a benchmark directory with every CBMC contract clause removed.

The committed benchmark programs under `eval/benchmarks/` already carry CBMC contracts from earlier
Avocado runs. To measure what the *system* produces from scratch, agent-time experiments run
`avocado-verify` on a copy of each benchmark whose top-level contract clauses
(`__CPROVER_requires`, `__CPROVER_ensures`, `__CPROVER_assigns`, `__CPROVER_frees`) have been
deleted. The benchmark files themselves are never modified: the stripped copy is written to a
separate destination directory.

Only whole clauses are removed; lines that become blank as a result are dropped so the stripped
source reads like an unannotated program. In-body intrinsics (e.g. `__CPROVER_assume`) are left
alone -- no benchmark currently contains any -- so the program text is otherwise unchanged.

Usage:
    % scripts/experiments/strip_specs.py <SRC_DIR> <DEST_DIR>
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.util.cbmc_clause_stripper import (  # ruff: ignore[module-import-not-at-top-of-file]
    CBMC_CLAUSE_NAMES,
    strip_cbmc_clauses,
)


def main() -> None:
    """Copy `src` to `dest`, removing contract clauses from every `.c` and `.h` file."""
    parser = argparse.ArgumentParser(description="Copy a benchmark with CBMC contracts removed.")
    parser.add_argument("src", help="Benchmark directory to copy (left untouched).")
    parser.add_argument("dest", help="Destination directory; created, or replaced if present.")
    args = parser.parse_args()

    src = Path(args.src).resolve()
    dest = Path(args.dest).resolve()
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns(".git", "*.goto", "*.json", "*.jsonl"))
    stripped = 0
    for path in sorted(dest.rglob("*")):
        if path.suffix not in {".c", ".h"} or not path.is_file():
            continue
        new_source, num_clauses = strip_specs(path.read_bytes())
        if num_clauses:
            path.write_bytes(new_source)
            stripped += num_clauses
            print(f"{path}: removed {num_clauses} clause(s)")
    print(f"removed {stripped} clause(s) in total; stripped copy at {dest}")


def strip_specs(source: bytes) -> tuple[bytes, int]:
    """Return `source` with every top-level contract clause deleted, plus the clause count.

    Clauses are located with `tools.util.cbmc_clause_stripper.strip_cbmc_clauses`. Each original
    line is reduced to the bytes that lie outside every clause span; a line that intersected a span
    and is left blank is dropped, while untouched lines are kept verbatim (blank or not).

    Args:
        source (bytes): The annotated C source.

    Returns:
        tuple[bytes, int]: The stripped source and the number of clauses removed.
    """
    _, spans = strip_cbmc_clauses(source)
    spans = [span for span in spans if span.kind in CBMC_CLAUSE_NAMES]
    if not spans:
        return source, 0

    kept: list[bytes] = []
    offset = 0
    for line in source.split(b"\n"):
        line_start, line_end = offset, offset + len(line)
        offset = line_end + 1  # Skip the newline.
        residual = bytearray()
        touched = False
        for index in range(line_start, line_end):
            if any(span.start_byte <= index < span.end_byte for span in spans):
                touched = True
            else:
                residual.append(source[index])
        if touched and residual.strip() == b"":
            continue
        kept.append(bytes(residual).rstrip() if touched else line)
    return b"\n".join(kept), len(spans)


if __name__ == "__main__":
    main()
