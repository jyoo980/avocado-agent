#!/usr/bin/env -S uv run --quiet python3

"""Attribute every second of an agent session to model generation or to a specific tool call.

Reads the Claude Code transcripts (`~/.claude/projects/<cwd>/<session>.jsonl`) of the sessions
named in one or more `<stem>-avocado-verify.jsonl` run logs and, for each session, walks the
timeline: a model turn runs from the previous event (the prompt, or the last tool result) to the
turn's final assistant entry; a tool call runs from its `tool_use` to the matching `tool_result`.
Sessions the harness killed on timeout have no terminal event, so their tail is charged to
whatever was in flight when the transcript stops, up to the harness timeout.

Prints, per arm, the split between model time and tool time (by tool kind), and the sessions that
cost the most, so the question "what is the wall-clock actually spent on" has a measured answer.

Usage:
    % scripts/experiments/session_timeline.py --projects-dir <DIR> <RUN_LOG>... \
          [--top N] [--timeout 1800]
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

_VERIFY = re.compile(r"avocado-run-cbmc")
_READ = re.compile(r"\b(cat|sed -n|head|tail|less|grep|rg|ls|find|wc)\b")
_EDIT = re.compile(r"sed -i|python3 -|>\s*\S+\.c\b|cat >")
_CBMC_BY_HAND = re.compile(r"\b(cbmc|goto-cc|goto-instrument)\b")


def _ts(value: str) -> datetime:
    """Parse a transcript timestamp.

    Args:
        value (str): An ISO-8601 timestamp as written by Claude Code.

    Returns:
        datetime: The parsed, timezone-aware timestamp.
    """
    return datetime.fromisoformat(value)


def _tool_kind(name: str, inp: dict) -> str:
    """Classify a tool call into the buckets the report uses.

    Args:
        name (str): The tool's name as recorded in the transcript.
        inp (dict): The tool's input; for `Bash`, its `command` decides the bucket.

    Returns:
        str: One of `read`, `edit`, `avocado-run-cbmc`, `cbmc-by-hand`, `background-wait`,
            `other-bash`, or `other:<tool>`.
    """
    if name in ("Read", "Glob", "Grep"):
        return "read"
    if name in ("Edit", "Write", "MultiEdit"):
        return "edit"
    if name in ("Monitor", "TaskOutput", "TaskStop"):
        return "background-wait"
    if name != "Bash":
        return f"other:{name}"
    cmd = inp.get("command", "")
    if _VERIFY.search(cmd):
        return "avocado-run-cbmc"
    if _CBMC_BY_HAND.search(cmd):
        return "cbmc-by-hand"
    if _EDIT.search(cmd):
        return "edit"
    if _READ.search(cmd):
        return "read"
    return "other-bash"


def analyse(path: Path, timeout: float, killed: bool) -> dict | None:
    """Return the time attribution for one session transcript.

    Args:
        path (Path): The transcript.
        timeout (float): The harness per-session timeout, charged to a killed session.
        killed (bool): Whether the harness killed this session on timeout (from the run log). A
            transcript cannot tell on its own: a normal session also ends on an assistant message.

    Returns:
        dict | None: The session's span, model time, per-kind tool time, turn count, killed flag,
            and its longest tool calls; None when the transcript cannot be read or is empty.
    """
    try:
        lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    except OSError:
        return None
    events = [r for r in lines if r.get("type") in ("user", "assistant") and r.get("timestamp")]
    if not events:
        return None
    model = 0.0
    tools: Counter[str] = Counter()
    turns = 0
    pending: dict[str, tuple[datetime, str, str]] = {}  # tool_use_id -> (start, kind, summary)
    last_event_ts = _ts(events[0]["timestamp"])
    turn_open = False
    turn_start = last_event_ts
    longest: list[tuple[float, str, str]] = []
    for r in events:
        ts = _ts(r["timestamp"])
        content = r["message"].get("content")
        if r["type"] == "assistant":
            if not turn_open:
                turn_open = True
                turn_start = last_event_ts
            if isinstance(content, list):
                for x in content:
                    if x.get("type") == "tool_use":
                        kind = _tool_kind(x["name"], x.get("input", {}))
                        summary = json.dumps(x.get("input", {}))[:90]
                        pending[x["id"]] = (ts, kind, summary)
            # the turn's model time is measured when the turn ends (next user event)
            last_assistant_ts = ts
        else:
            if turn_open:
                model += (last_assistant_ts - turn_start).total_seconds()
                turns += 1
                turn_open = False
            if isinstance(content, list):
                for x in content:
                    if x.get("type") == "tool_result" and x.get("tool_use_id") in pending:
                        start, kind, summary = pending.pop(x["tool_use_id"])
                        secs = (ts - start).total_seconds()
                        tools[kind] += secs
                        longest.append((secs, kind, summary))
            last_event_ts = ts
    start_ts = _ts(events[0]["timestamp"])
    end = _ts(events[-1]["timestamp"])
    span = (end - start_ts).total_seconds()
    if killed and pending:
        # Killed while a tool was running: the harness waited up to `timeout` in total.
        for start, kind, summary in pending.values():
            secs = max(0.0, timeout - (start - start_ts).total_seconds())
            tools[kind + " (killed)"] += secs
            longest.append((secs, kind + " (killed)", summary))
        span = timeout
    elif killed:
        # Killed while the model was generating.
        model += max(0.0, timeout - (turn_start - start_ts).total_seconds())
        span = timeout
    elif turn_open:
        # Ended normally on a final assistant message (usually the summary).
        model += (last_assistant_ts - turn_start).total_seconds()
        turns += 1
    longest.sort(reverse=True)
    return {
        "span": span,
        "model": model,
        "tools": tools,
        "turns": turns,
        "killed": killed,
        "longest": longest[:3],
    }


def main() -> None:
    """Attribute session time for the sessions named in the given run logs."""
    parser = argparse.ArgumentParser(description="Attribute agent session time to model vs tools.")
    parser.add_argument("run_logs", nargs="+", help="`<stem>-avocado-verify.jsonl` run logs.")
    parser.add_argument(
        "--projects-dir", required=True, help="Claude projects dir holding the transcripts."
    )
    parser.add_argument("--top", type=int, default=6, help="How many costliest sessions to list.")
    parser.add_argument(
        "--timeout", type=float, default=1800.0, help="Harness per-session timeout."
    )
    args = parser.parse_args()

    total_span = total_model = 0.0
    tool_totals: Counter[str] = Counter()
    sessions: list[tuple[float, str, dict]] = []
    for log in args.run_logs:
        for line in Path(log).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            if "type" in record:
                continue
            for s in record["claude"]:
                if not s.get("session_id"):
                    continue
                res = analyse(
                    Path(args.projects_dir) / f"{s['session_id']}.jsonl",
                    args.timeout,
                    bool(s.get("timed_out")),
                )
                if res is None:
                    continue
                total_span += res["span"]
                total_model += res["model"]
                tool_totals.update(res["tools"])
                sessions.append((res["span"], record["function"], res))

    tool_time = sum(tool_totals.values())
    print(f"sessions: {len(sessions)}   wall-clock: {total_span:,.0f}s")
    print(f"  model generation: {total_model:,.0f}s ({total_model / total_span:.0%})")
    print(f"  inside tools:     {tool_time:,.0f}s ({tool_time / total_span:.0%})")
    for kind, secs in tool_totals.most_common():
        print(f"      {kind:28s} {secs:8,.0f}s ({secs / total_span:5.1%})")
    other = total_span - total_model - tool_time
    print(f"  unattributed (gaps): {other:,.0f}s ({other / total_span:.0%})")
    print(f"\ncostliest {args.top} sessions:")
    sessions.sort(key=lambda t: -t[0])
    for span, fn, res in sessions[: args.top]:
        top_tools = ", ".join(f"{k} {v:.0f}s" for k, v in res["tools"].most_common(2))
        flag = "  [KILLED BY HARNESS]" if res["killed"] else ""
        print(
            f"  {fn:26s} {span:7,.0f}s  model {res['model']:6,.0f}s  "
            f"turns {res['turns']:3d}  {top_tools}{flag}"
        )
        for secs, kind, summary in res["longest"][:2]:
            print(f"        {secs:7,.0f}s {kind:22s} {summary}")


if __name__ == "__main__":
    main()
