#!/usr/bin/env python3
"""Report timing results for a single ``avocado-verify`` run over one C file.

``avocado-verify --file <program>.c`` leaves three artifacts behind that,
together, describe how long verification took and how it went:

* ``claude-output.json`` -- despite the name this is the plain console log
  (stdout+stderr, e.g. produced with ``avocado-verify --file lz4.c &> claude-output.json``).
  It is a stream of timestamped loguru lines.  It carries the true run
  *start* time, the original file path, and ``[i/N] <func>: VERIFIED`` markers.
* ``<program>-avocado-verify.jsonl`` -- one compact JSON object per verified
  function (its completion ``timestamp``, ``outcome``, ``verification_attempts``,
  ``total_cost_to_verify_usd``, per-session ``claude`` metadata), followed by a
  final ``{"type": "run_summary", ...}`` record.  This is the richest source.
* ``<program>-verification-attempts.jsonl`` -- a stream of (pretty-printed)
  JSON objects, one per time CBMC was fired, each with ``ts``/``function``/
  ``verified``.  Used to recover per-function attempt counts and whether the
  *first* attempt already passed.

This script accepts any combination of these (the console log and/or the
``-avocado-verify.jsonl``; the attempts file is auto-discovered next to the
latter when not given) and emits one JSON summary:

    {
      "file_name": "<path to the verified C file>",
      "total_time_to_verify": <int ms>,       # wall-clock, run start -> last function
      "functions_verified": <int>,            # (extra) from the run summary
      "functions_total": <int>,               # (extra)
      "functions": [
        {
          "name": "<function>",
          "cost": <float usd>,                # total_cost_to_verify_usd
          "time_taken_to_verify": <int ms>,   # wall-clock span for this function
          "is_verified": <bool>,
          "verification_attempts": <int|null>,# (extra) complexity proxy
          "first_attempt_verified": <bool|null> # (extra) did CBMC pass first try?
        },
        ...
      ]
    }

``time_taken_to_verify`` and ``total_time_to_verify`` are durations in
milliseconds (wall-clock spans between the completion timestamps the logs
already record), per the benchmarking use case -- not absolute epoch stamps.
The per-function times sum to ``total_time_to_verify``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, Optional

# ----------------------------------------------------------------------------
# Low-level parsing helpers
# ----------------------------------------------------------------------------

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


def _parse_iso(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp (``...Z`` or ``+00:00``) to an aware UTC datetime."""
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_console_ts(ts: str) -> datetime:
    """Parse a loguru console timestamp ('2026-09-14 18:43:40.077'); assumed UTC.

    The console log's clock agrees with the UTC timestamps in the JSONL logs
    (the first console line precedes the first function completion by seconds),
    so we treat these naive stamps as UTC for cross-source spans.
    """
    dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S.%f")
    return dt.replace(tzinfo=timezone.utc)


def _epoch_ms(dt: datetime) -> int:
    return int(round(dt.timestamp() * 1000))


def _iter_json_objects(text: str) -> Iterator[dict]:
    """Yield each JSON object from *text*, robust to both one-per-line and
    concatenated pretty-printed streams (uses raw_decode over the whole blob)."""
    decoder = json.JSONDecoder()
    idx = 0
    n = len(text)
    while idx < n:
        # Skip whitespace (and stray separators) between objects.
        while idx < n and text[idx] in " \t\r\n":
            idx += 1
        if idx >= n:
            break
        try:
            obj, end = decoder.raw_decode(text, idx)
        except json.JSONDecodeError:
            # Advance to the next plausible object start to stay resilient.
            nxt = text.find("{", idx + 1)
            if nxt == -1:
                break
            idx = nxt
            continue
        if isinstance(obj, dict):
            yield obj
        idx = end


# ----------------------------------------------------------------------------
# Source-specific loaders
# ----------------------------------------------------------------------------


class ConsoleLog:
    """Parsed view of the ``claude-output.json`` console log."""

    def __init__(self, text: str):
        self.start: Optional[datetime] = None
        self.end: Optional[datetime] = None
        self.file_path: Optional[str] = None
        self.verified_count: Optional[int] = None
        self.total_count: Optional[int] = None
        # Ordered (name, is_verified, completion_ts) from "[i/N] func: STATUS".
        self.status_events: list[tuple[str, bool, datetime]] = []

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
                is_verified = m_status.group(4) == "VERIFIED"
                # "generating" would be excluded by the UPPER_SNAKE match above.
                self.status_events.append((name, is_verified, ts))

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


# ----------------------------------------------------------------------------
# Core assembly
# ----------------------------------------------------------------------------


def build_report(
    verify_records: Optional[list[dict]],
    console: Optional[ConsoleLog],
    attempts: Optional[dict[str, list[bool]]],
    attempts_start: Optional[datetime] = None,
    file_name_hint: Optional[str] = None,
) -> dict:
    attempts = attempts or {}

    # ---- Per-function base rows (name, is_verified, cost, completion_ts, ...) --
    rows: list[dict] = []
    run_summary: Optional[dict] = None

    if verify_records:
        for rec in verify_records:
            if rec.get("type") == "run_summary":
                run_summary = rec
                continue
            name = rec.get("function")
            if name is None:
                continue
            claude_sessions = rec.get("claude") or []
            agent_ms = sum(int(s.get("duration_ms") or 0) for s in claude_sessions)
            rows.append(
                {
                    "name": name,
                    "cost": rec.get("total_cost_to_verify_usd"),
                    "is_verified": rec.get("outcome") == "VERIFIED",
                    "completion_ts": _parse_iso(rec["timestamp"]),
                    "verification_attempts": rec.get("verification_attempts"),
                    "agent_duration_ms": agent_ms,
                }
            )
    elif console and console.status_events:
        # Fall back to the console log's per-function verdicts (no cost available).
        for name, is_verified, ts in console.status_events:
            rows.append(
                {
                    "name": name,
                    "cost": None,
                    "is_verified": is_verified,
                    "completion_ts": ts,
                    "verification_attempts": None,
                    "agent_duration_ms": None,
                }
            )
    else:
        raise SystemExit(
            "error: need either a -avocado-verify.jsonl or a claude-output console "
            "log with per-function verdicts to produce a report."
        )

    rows.sort(key=lambda r: r["completion_ts"])

    # ---- Run start / end -----------------------------------------------------
    first_completion = rows[0]["completion_ts"]
    last_completion = rows[-1]["completion_ts"]

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

    run_end = last_completion  # per-function spans sum exactly to the total.

    total_ms = _epoch_ms(run_end) - _epoch_ms(run_start)

    # ---- Per-function wall-clock spans --------------------------------------
    functions = []
    prev_boundary = run_start
    for row in rows:
        span_ms = _epoch_ms(row["completion_ts"]) - _epoch_ms(prev_boundary)
        prev_boundary = row["completion_ts"]

        attempt_flags = attempts.get(row["name"])
        n_attempts = row["verification_attempts"]
        if n_attempts is None and attempt_flags is not None:
            n_attempts = len(attempt_flags)

        if attempt_flags:
            first_attempt_verified: Optional[bool] = attempt_flags[0]
        elif n_attempts == 1:
            # The only attempt's result is the function's result.
            first_attempt_verified = row["is_verified"]
        else:
            first_attempt_verified = None

        functions.append(
            {
                "name": row["name"],
                "cost": None if row["cost"] is None else round(row["cost"], 3),
                "time_taken_to_verify": span_ms,
                "is_verified": row["is_verified"],
                "verification_attempts": n_attempts,
                "first_attempt_verified": first_attempt_verified,
            }
        )

    # ---- File name -----------------------------------------------------------
    if console and console.file_path:
        file_name = console.file_path
    else:
        file_name = file_name_hint

    # ---- Verified / total counts --------------------------------------------
    if run_summary is not None:
        verified_count = run_summary.get("verified")
        total_count = run_summary.get("total")
    elif console and console.verified_count is not None:
        verified_count = console.verified_count
        total_count = console.total_count
    else:
        verified_count = sum(1 for f in functions if f["is_verified"])
        total_count = len(functions)

    return {
        "file_name": file_name,
        "total_time_to_verify": total_ms,
        "functions_verified": verified_count,
        "functions_total": total_count,
        "functions": functions,
    }


def _earliest_attempt_ts(text: str) -> Optional[datetime]:
    earliest: Optional[datetime] = None
    for obj in _iter_json_objects(text):
        ts = obj.get("ts")
        if not ts:
            continue
        dt = _parse_iso(ts)
        if earliest is None or dt < earliest:
            earliest = dt
    return earliest


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------


def _classify(path: Path) -> str:
    name = path.name
    if name.endswith("-avocado-verify.jsonl"):
        return "verify"
    if name.endswith("-verification-attempts.jsonl"):
        return "attempts"
    return "console"


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Report timing results for one avocado-verify run.",
    )
    parser.add_argument(
        "inputs",
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
    args = parser.parse_args(argv)

    console_path = args.claude_output
    verify_path = args.avocado_verify
    attempts_path = args.attempts

    for path in args.inputs:
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

    report = build_report(
        verify_records, console, attempts, attempts_start, file_name_hint
    )

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


if __name__ == "__main__":
    raise SystemExit(main())
