#!/usr/bin/env python3

"""Print the number of source lines of code (SLOC) of every function in one or more C files.

A line counts as a source line of code iff it carries at least one non-comment token lying within
a function body, i.e. strictly between the body's enclosing braces. The signature, CBMC contract
clauses (`__CPROVER_requires(...)`, ...), the enclosing braces, blank lines, comment-only lines and
the contents of `#if 0` blocks are therefore excluded; lines mixing code with a trailing comment
are included.

Files are parsed as-is with tree-sitter: `#include` directives are not followed and macros are not
expanded. A function defined several times in a file (typically in alternative preprocessor
branches) is reported once per definition, with its line range telling the definitions apart.

Usage:
    % avocado-count-sloc <PATH_TO_C_FILE> [<PATH_TO_C_FILE> ...] [--json]

By default one tab-separated line `file<TAB>function<TAB>lines<TAB>sloc` is printed per function
definition, followed by a per-file total. With `--json`, a JSON object mapping each file path to
its list of function records is printed instead.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from tools.util import FunctionSloc, get_function_sloc


def main() -> None:
    """Count source lines of code per function in the given C files and print the result."""
    parser = argparse.ArgumentParser(
        description="Count source lines of code (SLOC) per function in one or more C files."
    )
    parser.add_argument(
        "paths_to_files",
        nargs="+",
        metavar="PATH_TO_C_FILE",
        help="Path(s) to the C file(s) whose functions to measure.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print a JSON object mapping each file to its function records instead of text.",
    )
    args = parser.parse_args()

    missing = [path for path in args.paths_to_files if not Path(path).is_file()]
    if missing:
        parser.error(f"no such file(s): {', '.join(missing)}")

    sloc_by_file = count_sloc(args.paths_to_files)
    if args.json:
        print(format_json(sloc_by_file))
    else:
        print(format_text(sloc_by_file), end="")


def count_sloc(paths_to_files: list[str]) -> dict[str, list[FunctionSloc]]:
    """Return the per-function SLOC of every given file, keyed by file path.

    Args:
        paths_to_files (list[str]): Paths to the C files to measure.

    Returns:
        dict[str, list[FunctionSloc]]: For each file path (in the given order), one record per
            function definition in source order.
    """
    return {path: get_function_sloc(path) for path in paths_to_files}


def format_text(sloc_by_file: dict[str, list[FunctionSloc]]) -> str:
    """Return a tab-separated rendering of the given SLOC counts, one function per line.

    Each file's functions are followed by a `<total>` line summing their SLOC. A function whose
    SLOC could not be counted is rendered as `?`. The total sums only the known counts; when
    some are unknown it is marked as partial, e.g. `27 (+2 unknown)`.

    Args:
        sloc_by_file (dict[str, list[FunctionSloc]]): Per-file function records.

    Returns:
        str: The rendered table, ending in a newline (or empty if there are no files).
    """
    lines: list[str] = []
    for path, records in sloc_by_file.items():
        lines.extend(
            f"{path}\t{record.name}\t{record.start_line}-{record.end_line}\t{_render(record.sloc)}"
            for record in records
        )
        lines.append(f"{path}\t<total>\t{len(records)} function(s)\t{_render_total(records)}")
    return "".join(f"{line}\n" for line in lines)


def _render_total(records: list[FunctionSloc]) -> str:
    """Return the summed SLOC of the given records for display, flagging unknown counts.

    Args:
        records (list[FunctionSloc]): The records to sum.

    Returns:
        str: The sum of the known counts, followed by ` (+N unknown)` if N > 0 records have no
            count.
    """
    known = [record.sloc for record in records if record.sloc is not None]
    unknown = len(records) - len(known)
    total = str(sum(known))
    return f"{total} (+{unknown} unknown)" if unknown else total


def _render(sloc: int | None) -> str:
    """Return a SLOC count for display, using `?` for an unknown count.

    Args:
        sloc (int | None): The SLOC count, or None if it could not be determined.

    Returns:
        str: The count as text, or `?` if unknown.
    """
    return "?" if sloc is None else str(sloc)


def format_json(sloc_by_file: dict[str, list[FunctionSloc]]) -> str:
    """Return a JSON rendering of the given SLOC counts.

    Args:
        sloc_by_file (dict[str, list[FunctionSloc]]): Per-file function records.

    Returns:
        str: A JSON object mapping each file path to a list of
            `{"name", "start_line", "end_line", "sloc"}` objects.
    """
    return json.dumps(
        {path: [asdict(record) for record in records] for path, records in sloc_by_file.items()},
        indent=4,
    )


if __name__ == "__main__":
    sys.exit(main())
