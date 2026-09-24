#!/usr/bin/env python3
"""Report timing results for a single ``avocado-verify`` run over one C file.

``avocado-verify --file <program>.c`` creates three log files that, together,
describe how long verification took and how it went:

* ``claude-output.json`` -- plain console log
  (stdout+stderr, e.g. produced with ``avocado-verify --file lz4.c &> claude-output.json``).
  It is a stream of timestamped loguru lines.  It carries the true run
  *start* time, the original file path, and ``[i/N] <func>: VERIFIED`` markers.
* ``<program>-avocado-verify.jsonl`` -- one JSON object per verified function
  (its completion ``timestamp``, ``outcome``, ``verification_attempts``,
  ``total_cost_to_verify_usd``, per-session ``claude`` metadata), followed by a
  final ``{"type": "run_summary", ...}`` record.  This is the richest source.
* ``<program>-verification-attempts.jsonl`` -- a stream of JSON objects, one
  per time CBMC was fired, each with ``ts``/``function``/``verified``.
  Used to recover per-function attempt counts and whether the *first*
  attempt already passed.

This script accepts any combination of these (the console log and/or the
``-avocado-verify.jsonl``; the attempts file is auto-discovered next to the
latter when not given) and emits one JSON summary:

    {
      "file_name": "<path to the verified C file>",
      "total_time_to_verify": <int ms>,       # wall-clock, run start -> last function
      "functions_verified": <int>,
      "functions_total": <int>,
      "functions": [
        {
          "name": "<function>",
          "cost": <float usd>,                # total_cost_to_verify_usd
          "time_taken_to_verify": <int ms>,   # wall-clock span for this function
          "is_verified": <bool>,
          "timed_out": <bool>,                # agent session or CBMC step timed out
          "verification_attempts": <int|null>,
          "first_attempt_verified": <bool|null> # did CBMC pass first try when run in agent sandbox?
        },
        ...
      ]
    }

``time_taken_to_verify`` and ``total_time_to_verify`` are durations in
milliseconds (wall-clock spans between the completion timestamps the logs
already record) -- not absolute epoch stamps. The per-function times sum
to ``total_time_to_verify``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from log_parse_util import _epoch_ms, _iter_json_objects, _parse_console_ts, _parse_iso

# Matches the loguru prefix of a console line: "2026-09-14 18:43:40.077 | ".
_CONSOLE_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+)\b")
# Matches a per-function terminal verdict, e.g. "[1/91] LZ4_isAligned: VERIFIED"
# or "[.../91] foo: CLAUDE_TIMED_OUT". The status is an UPPER_SNAKE token, which
# excludes the lowercase "[i/N] foo: generating spec via claude -p" start line.
_CONSOLE_STATUS_RE = re.compile(r"\[(\d+)/(\d+)\]\s+(\w+):\s+([A-Z][A-Z_]+)\s*$")
# Matches the final "... log written to /app/lz4_lib/lz4-avocado-verify.jsonl".
_CONSOLE_LOGPATH_RE = re.compile(r"log written to (\S+-avocado-verify\.jsonl)")
# Matches "25/91 function(s) verified".
_CONSOLE_SUMMARY_RE = re.compile(r"(\d+)/(\d+) function\(s\) verified")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Report timing results for one avocado-verify run.",
    )
    parser.add_argument(
        "log_files",
        nargs="*",
        type=Path,
        help="Any of: claude-output console log, <prog>-avocado-verify.jsonl, "
        "<prog>-verification-attempts.jsonl (auto-classified by name).",
    )
    parser.add_argument("--claude-output", type=Path, help="Console log path.")
    parser.add_argument("--avocado-verify", type=Path, help="<prog>-avocado-verify.jsonl path.")
    parser.add_argument("--attempts", type=Path, help="<prog>-verification-attempts.jsonl path.")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Where to write the JSON report (default: <prog>-time-benchmarks.json "
        "next to the -avocado-verify.jsonl, else stdout).",
    )
    args = parser.parse_args()

    console_path = args.claude_output
    verify_path = args.avocado_verify
    attempts_path = args.attempts

    for path in args.log_files:
        kind = _classify(path)
        if kind == "verify" and verify_path is None:
            verify_path = path
        elif kind == "attempts" and attempts_path is None:
            attempts_path = path
        elif kind == "console" and console_path is None:
            console_path = path

    if verify_path is None and console_path is None:
        parser.error("provide at least a -avocado-verify.jsonl or a console log.")

    # Auto-discover a sibling attempts file next to the verify log.
    if attempts_path is None and verify_path is not None:
        stem = verify_path.name[: -len("-avocado-verify.jsonl")]
        candidate = verify_path.with_name(stem + "-verification-attempts.jsonl")
        if candidate.exists():
            attempts_path = candidate

    verify_records = None
    if verify_path is not None:
        verify_records = list(_iter_json_objects(verify_path.read_text()))

    console = None
    if console_path is not None:
        console = ConsoleLog(console_path.read_text())

    attempts = None
    attempts_start = None
    if attempts_path is not None:
        attempts_text = attempts_path.read_text()
        attempts = _attempts_by_function(attempts_text)
        attempts_start = _earliest_attempt_ts(attempts_text)

    # If the console log did not supply the file path, derive it from the
    # "<stem>-avocado-verify.jsonl" filename (-> "<stem>.c").
    file_name_hint = None
    if verify_path is not None:
        stem = verify_path.name[: -len("-avocado-verify.jsonl")]
        file_name_hint = stem + ".c"

    report = build_report(verify_records, console, attempts, attempts_start, file_name_hint)

    text = json.dumps(report, indent=2)
    out_path = args.output
    if out_path is None and verify_path is not None:
        stem = verify_path.name[: -len("-avocado-verify.jsonl")]
        out_path = verify_path.with_name(stem + "-time-benchmarks.json")

    if out_path is None:
        print(text)
    else:
        out_path.write_text(text + "\n")
        print(f"wrote {out_path}")
    return 0


def _classify(path: Path) -> str:
    name = path.name
    if name.endswith("-avocado-verify.jsonl"):
        return "verify"
    if name.endswith("-verification-attempts.jsonl"):
        return "attempts"
    return "console"


class ConsoleLog:
    """Parsed view of the ``claude-output.json`` console log."""

    def __init__(self, text: str):
        self.start: datetime | None = None
        self.end: datetime | None = None
        self.file_path: str | None = None
        self.verified_count: int | None = None
        self.total_count: int | None = None
        self.status_events: list[tuple[str, str, datetime]] = []

        for line in text.splitlines():
            m_ts = _CONSOLE_TS_RE.match(line)
            ts = _parse_console_ts(m_ts.group(1)) if m_ts else None
            if ts is not None:
                if self.start is None:
                    self.start = ts
                self.end = ts

            m_status = _CONSOLE_STATUS_RE.search(line)
            if m_status and ts is not None:
                name = m_status.group(3)
                status = m_status.group(4)
                # "generating" would be excluded by the UPPER_SNAKE match above.
                self.status_events.append((name, status, ts))

            m_path = _CONSOLE_LOGPATH_RE.search(line)
            if m_path:
                jsonl_path = m_path.group(1)
                self.file_path = _c_file_from_jsonl(jsonl_path)

            m_sum = _CONSOLE_SUMMARY_RE.search(line)
            if m_sum:
                self.verified_count = int(m_sum.group(1))
                self.total_count = int(m_sum.group(2))


def _c_file_from_jsonl(jsonl_path: str) -> str:
    """Map '.../<stem>-avocado-verify.jsonl' back to '.../<stem>.c'."""
    p = Path(jsonl_path)
    stem = p.name[: -len("-avocado-verify.jsonl")]
    return str(p.with_name(stem + ".c"))


def _attempts_by_function(text: str) -> dict[str, list[bool]]:
    """Group ``-verification-attempts.jsonl`` records by function, preserving order.

    Returns {function: [verified_flag_of_each_CBMC_firing, ...]}.
    """
    groups: dict[str, list[bool]] = {}
    for obj in _iter_json_objects(text):
        fn = obj.get("function")
        if fn is None:
            continue
        groups.setdefault(fn, []).append(bool(obj.get("verified", False)))
    return groups


def _earliest_attempt_ts(text: str) -> datetime | None:
    return min(
        (_parse_iso(ts) for obj in _iter_json_objects(text) if (ts := obj.get("ts"))),
        default=None,
    )


def build_report(
    verify_records: list[dict],
    console: ConsoleLog,
    attempts: dict[str, list[bool]],
    attempts_start: datetime,
    file_name_hint: str,
) -> dict:
    attempts = attempts or {}

    rows: list[dict] = []  # each row represents a single record per function
    run_summary: dict | None = None

    if verify_records:
        for rec in verify_records:
            if _is_run_summary(rec):
                run_summary = rec
                continue
            if rec.get("function") is None:  # skip malformed/nameless records
                continue
            rows.append(_construct_row(rec))

    elif console and console.status_events:
        # Fall back to the console log's per-function verdicts (no cost available)
        for name, status, ts in console.status_events:
            rows.append(
                {
                    "name": name,
                    "cost": None,
                    "is_verified": status == "VERIFIED",
                    "completion_ts": ts,
                    "verification_attempts": None,
                    "agent_duration_ms": None,
                    "timed_out": status == "CLAUDE_TIMED_OUT",
                }
            )
    else:
        raise SystemExit(
            "error: need either a -avocado-verify.jsonl or a claude-output console "
            "log with per-function verdicts to produce a report."
        )

    rows.sort(key=lambda r: r["completion_ts"])

    run_start = _resolve_run_start(rows, console, attempts_start)
    run_end = rows[-1]["completion_ts"]
    total_ms = _epoch_ms(run_end) - _epoch_ms(run_start)  # total run time

    functions = []
    prev_boundary = run_start
    for row in rows:
        # Constructs record for each function.
        functions.append(_construct_function_record(row, prev_boundary, attempts))
        prev_boundary = row["completion_ts"]

    if console and console.file_path:
        file_name = console.file_path
    else:
        file_name = file_name_hint

    verified_count, total_count = _resolve_verification_counts(run_summary, console, functions)

    report = {
        "file_name": file_name,
        "total_time_to_verify": total_ms,
        "functions_verified": verified_count,
        "functions_total": total_count,
        "functions": functions,
    }

    return report


def _is_run_summary(rec: dict) -> bool:
    return rec.get("type") == "run_summary"


def _construct_row(rec: dict) -> dict:
    name = rec["function"]
    claude_sessions = rec.get("claude") or []
    agent_ms = sum(int(s.get("duration_ms") or 0) for s in claude_sessions)
    # A run timed out if any agent session or the CBMC step timed out.
    timed_out = any(bool(s.get("timed_out")) for s in claude_sessions) or bool(
        (rec.get("cbmc") or {}).get("timed_out")
    )

    row = {
        "name": name,
        "cost": rec.get("total_cost_to_verify_usd"),
        "is_verified": rec.get("outcome") == "VERIFIED",
        "completion_ts": _parse_iso(rec["timestamp"]),
        "verification_attempts": rec.get("verification_attempts"),
        "agent_duration_ms": agent_ms,
        "timed_out": timed_out,
    }

    return row


def _resolve_run_start(
    rows: list[dict], console: ConsoleLog | None, attempts_start: datetime | None
) -> int:
    first_completion = rows[0]["completion_ts"]

    run_start = console.start if console and console.start else None
    if run_start is None and attempts_start is not None:
        # Earliest CBMC firing is the next-best lower bound on the run start.
        run_start = attempts_start
    if run_start is None or run_start > first_completion:
        if run_start is not None and run_start > first_completion:
            print(
                "warning: run start is after the first completion; clamping.",
                file=sys.stderr,
            )
        run_start = first_completion

    return run_start


def _construct_function_record(
    row: dict, prev_boundary: datetime, attempts: dict[str, list[bool]]
) -> dict:
    span_ms = _epoch_ms(row["completion_ts"]) - _epoch_ms(prev_boundary)

    attempt_flags = attempts.get(row["name"])
    n_attempts = row["verification_attempts"]
    if n_attempts is None and attempt_flags is not None:
        n_attempts = len(attempt_flags)

    if attempt_flags:
        first_attempt_verified: bool | None = attempt_flags[0]
    elif n_attempts == 1:
        # The only attempt's result is the function's result.
        first_attempt_verified = row["is_verified"]
    else:
        first_attempt_verified = None

    function_record = {
        "name": row["name"],
        "cost": None if row["cost"] is None else round(row["cost"], 3),
        "time_taken_to_verify": span_ms,
        "is_verified": row["is_verified"],
        "timed_out": row["timed_out"],
        "verification_attempts": n_attempts,
        "first_attempt_verified": first_attempt_verified,
    }

    return function_record


def _resolve_verification_counts(
    run_summary: dict | None, console: ConsoleLog | None, functions: list[dict]
) -> tuple[int, int]:  # (verified_count, total_count)
    if run_summary is not None:
        verified_count = run_summary.get("verified")
        total_count = run_summary.get("total")
    elif console and console.verified_count is not None:
        verified_count = console.verified_count
        total_count = console.total_count
    else:
        verified_count = sum(1 for f in functions if f["is_verified"])
        total_count = len(functions)

    return verified_count, total_count


if __name__ == "__main__":
    main()
