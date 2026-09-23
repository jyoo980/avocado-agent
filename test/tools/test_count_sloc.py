"""Tests for the per-function SLOC counting script."""

import json

from tools.count_sloc import count_sloc, format_json, format_text
from tools.util import FunctionSloc


def test_count_sloc_over_multiple_files_keeps_argument_order() -> None:
    paths = ["test/data/quicksort.c", "test/data/no_callees.c"]
    result = count_sloc(paths)
    assert list(result) == paths
    assert [record.name for record in result["test/data/quicksort.c"]] == [
        "swap",
        "partition",
        "quickSort",
    ]


def test_format_text_has_one_row_per_function_plus_total() -> None:
    result = count_sloc(["test/data/quicksort.c"])
    rows = format_text(result).splitlines()
    assert rows == [
        "test/data/quicksort.c\tswap\t5-15\t3",
        "test/data/quicksort.c\tpartition\t18-50\t10",
        "test/data/quicksort.c\tquickSort\t53-72\t5",
        "test/data/quicksort.c\t<total>\t3 function(s)\t18",
    ], rows


def test_format_text_renders_unknown_sloc_and_marks_total_as_partial() -> None:
    result = {
        "a.c": [
            FunctionSloc(name="known", start_line=1, end_line=3, sloc=3),
            FunctionSloc(name="unknown", start_line=5, end_line=9, sloc=None),
            FunctionSloc(name="also_unknown", start_line=11, end_line=12, sloc=None),
        ],
        "b.c": [FunctionSloc(name="only", start_line=1, end_line=1, sloc=1)],
    }
    rows = format_text(result).splitlines()
    assert rows == [
        "a.c\tknown\t1-3\t3",
        "a.c\tunknown\t5-9\t?",
        "a.c\talso_unknown\t11-12\t?",
        "a.c\t<total>\t3 function(s)\t3 (+2 unknown)",
        "b.c\tonly\t1-1\t1",
        "b.c\t<total>\t1 function(s)\t1",
    ], rows


def test_format_json_renders_unknown_sloc_as_null() -> None:
    result = {"a.c": [FunctionSloc(name="unknown", start_line=5, end_line=9, sloc=None)]}
    parsed = json.loads(format_json(result))
    assert parsed == {"a.c": [{"name": "unknown", "start_line": 5, "end_line": 9, "sloc": None}]}, (
        parsed
    )


def test_format_json_round_trips() -> None:
    result = count_sloc(["test/data/quicksort.c"])
    parsed = json.loads(format_json(result))
    assert parsed == {
        "test/data/quicksort.c": [
            {"name": "swap", "start_line": 5, "end_line": 15, "sloc": 3},
            {"name": "partition", "start_line": 18, "end_line": 50, "sloc": 10},
            {"name": "quickSort", "start_line": 53, "end_line": 72, "sloc": 5},
        ]
    }, parsed
