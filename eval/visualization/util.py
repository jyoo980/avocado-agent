"""Shared utilities for spec-quality visualization scripts."""

from __future__ import annotations

import json
import shutil
from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

from enum import StrEnum
from pathlib import Path

from loguru import logger


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
            summary kinds.
    """
    summary_groups = defaultdict(list)
    for record in records:
        match record.get("kind"):
            case supported_kind if supported_kind in {
                summary.value for summary in SpecificationQualitySummary
            }:
                summary_groups[supported_kind].append(record)
            case unsupported_kind:
                logger.warning(f"Unknown record kind: {unsupported_kind!r}")
    return summary_groups


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
