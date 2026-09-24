"""Parses the three log files that describe time and verification data.

* ``claude-output.json`` -- plain console log
* ``<program>-avocado-verify.jsonl`` -- one JSON object per verified function
* ``<program>-verification-attempts.jsonl`` -- attempts log

Low-level parsing helper file for ``get_time_benchmarks.py``.
"""

import json
from collections.abc import Iterator
from datetime import UTC, datetime


def parse_iso(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp to a UTC datetime.

    Returns:
        datetime: ISO-8601 timestamp.
    """
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def parse_console_ts(ts: str) -> datetime:
    """Parse a loguru console timestamp ('2026-09-14 18:43:40.077').

    The console log's clock agrees with the UTC timestamps in the JSONL logs
    so we treat these naive stamps as UTC for cross-source spans.

    Returns:
        datetime: loguru timestamp.
    """
    return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=UTC)


def epoch_ms(dt: datetime) -> int:
    """Round epoch to milliseconds.

    Returns:
        int: epoch rounded to milliseconds.
    """
    return round(dt.timestamp() * 1000)


def iter_json_objects(text: str) -> Iterator[dict]:
    """Yield each JSON object from a stream of concatenated JSON values.

    Handles both newline-delimited JSON and pretty-printed objects run together,
    skipping whitespace between them and resyncing to the next ``{`` on malformed input.

    Yields:
        dict: Each top-level JSON object parsed from ``text``, in order.
    """
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
