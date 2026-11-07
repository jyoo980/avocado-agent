"""Shared utilities for spec-quality visualization scripts."""

from __future__ import annotations

import json
import shutil
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

from enum import StrEnum
from pathlib import Path

from loguru import logger

# A function's (file, function) pair, such as ("/benchmarks/q.c", "f").
FunctionKey = tuple[str, str]


class SpecificationQualitySummary(StrEnum):
    """Represent JSON records that are parsed by visualization scripts."""

    SUMMARY_FOR_MUTATION_TESTING = "mutation_summary"
    SUMMARY_FOR_CLAUSE_REDUNDANCY = "clause_redundancy_summary"


def yield_records(path_to_jsonl: str) -> Iterator[dict]:
    """Yield each well-formed JSON object from a JSONL file.

    Malformed lines are logged and skipped rather than raising, so a single
    bad row does not abort an interactive plotting session.

    Args:
        path_to_jsonl (str): Path to the JSONL file to parse records from.

    Yields:
        Iterator[dict]: An iterator of each JSON record parsed from the JSONL file.
    """
    with Path(path_to_jsonl).open("r", encoding="utf-8") as handle:
        for line_num, raw in enumerate(handle, start=1):
            json_line = raw.strip()
            if not json_line:
                continue
            try:
                yield json.loads(json_line)
            except json.JSONDecodeError as exc:
                logger.warning(f"{path_to_jsonl}:{line_num}: skipping malformed JSON ({exc})")


def group_by_summary_type(records: Iterable[dict]) -> dict[SpecificationQualitySummary, list[dict]]:
    """Return records grouped by specification quality summary kinds.

    Args:
        records (Iterable[dict]): The records to group.

    Returns:
        dict[SpecificationQualitySummary, list[dict]]: Records grouped by specification quality
            summary kinds.  Every summary kind is a key, even if no record has that kind.
    """
    summary_groups: dict[SpecificationQualitySummary, list[dict]] = {
        summary: [] for summary in SpecificationQualitySummary
    }
    for record in records:
        match record.get("kind"):
            case supported_kind if supported_kind in {
                summary.value for summary in SpecificationQualitySummary
            }:
                summary_groups[SpecificationQualitySummary(supported_kind)].append(record)
            case unsupported_kind:
                logger.warning(f"Unknown record kind: {unsupported_kind!r}")
    return summary_groups


def get_kill_scored_records(records: Iterable[dict]) -> list[dict]:
    """Return the mutation summary records that have a kill score, with each kill score a float.

    A record has no kill score if its function was not mutation tested (for example, because the
    function did not verify or had no mutants), or if none of its mutants was decided (each one
    timed out or failed to compile).  The `kill_score` of a record with no decided mutants is null
    or a string such as "0.0000", and it does not measure the spec.

    Args:
        records (Iterable[dict]): mutation summary records, each a dict parsed from a JSONL line
            with keys such as "file", "function", "killed", "survived", and "kill_score".

    Returns:
        list[dict]: Copies of the records that have a kill score, in which `kill_score` is a float
            even if the JSONL line stores it as a string such as "0.2500".
    """
    return [
        {**record, "kill_score": float(record["kill_score"])}
        for record in records
        if record.get("kill_score") is not None and record["killed"] + record["survived"] > 0
    ]


def get_function_key(record: dict) -> FunctionKey:
    """Return the `FunctionKey` of the given record.

    Args:
        record (dict): a record, as a dict parsed from a JSONL line with keys such as "file" and
            "function".

    Returns:
        FunctionKey: The record's (file, function) pair, such as ("/benchmarks/q.c", "f").
    """
    return (record["file"], record["function"])


def ascii_hbar(value: float, width: int, fill: str = "█") -> str:
    """Return a horizontal bar of `width` columns for a value in [0, 1].

    Args:
        value (float): The value (height) of the bar.
        width (int): The width of the bar (columns).
        fill (str): The fill of the bar.

    Returns:
        str: A horizontal bar of `width` columns for a value in [0, 1].
    """
    clamped = max(0.0, min(1.0, value))
    filled = round(clamped * width)
    return fill * filled + " " * (width - filled)


def get_terminal_width(default: int = 80) -> int:
    """Return the terminal width, fall back to the given default when unavailable.

    Args:
        default (int, 80): The default width of the terminal.

    Returns:
        int: The terminal width, fall back to the given default when unavailable.
    """
    try:
        return shutil.get_terminal_size().columns
    except OSError:
        return default


def get_label_for_record(record: dict) -> str:
    """Return a `file#function` label, with the filename basename only.

    Args:
        record (dict): The record for which to construct a label.

    Returns:
        str: A `file#function` label, with the filename basename only.
    """
    return f"{Path(record['file']).name}#{record['function']}"
