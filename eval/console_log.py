"""Parse the ``claude-output.json`` console log from an ``avocado-verify`` run.

Provides ``ConsoleLog``, which extracts the run's start/end times, per-function
verdicts, the verified C file path, and the verified/total counts from the
timestamped loguru lines ``avocado-verify`` writes to stdout/stderr.
"""

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

from eval.log_parse_util import c_file_from_jsonl, parse_console_ts

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


class ConsoleLog:
    """Parsed view of the ``claude-output.json`` console log."""

    def __init__(self, text: str):
        """Parse a console log into the run's timing, per-function verdicts, path, and counts.

        Args:
            text (str): Full console output (stdout+stderr) from an ``avocado-verify`` run.
                i.e. the contents of the ``claude-output.json`` file.
        """
        self.start: datetime | None = None
        self.end: datetime | None = None
        self.file_path: str | None = None
        self.verified_count: int | None = None
        self.total_count: int | None = None
        self.status_events: list[tuple[str, str, datetime]] = []

        for line in text.splitlines():
            ts_match = _CONSOLE_TS_RE.match(line)
            ts = parse_console_ts(ts_match.group(1)) if ts_match else None
            if ts is not None:
                if self.start is None:
                    self.start = ts
                self.end = ts

            status_match = _CONSOLE_STATUS_RE.search(line)
            if status_match and ts is not None:
                name = status_match.group(3)
                status = status_match.group(4)
                # "generating" would be excluded by the UPPER_SNAKE match above.
                self.status_events.append((name, status, ts))

            path_match = _CONSOLE_LOGPATH_RE.search(line)
            if path_match:
                jsonl_path = path_match.group(1)
                self.file_path = c_file_from_jsonl(jsonl_path)

            sum_match = _CONSOLE_SUMMARY_RE.search(line)
            if sum_match:
                self.verified_count = int(sum_match.group(1))
                self.total_count = int(sum_match.group(2))
