"""Tests for the per-function SLOC counting script."""

import json

from tools.count_sloc import count_sloc, format_json, format_text


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
        "test/data/quicksort.c\tswap\t5-15\t6",
        "test/data/quicksort.c\tpartition\t18-50\t13",
        "test/data/quicksort.c\tquickSort\t53-72\t8",
        "test/data/quicksort.c\t<total>\t3 function(s)\t27",
    ], rows


def test_format_json_round_trips() -> None:
    result = count_sloc(["test/data/quicksort.c"])
    parsed = json.loads(format_json(result))
    assert parsed == {
        "test/data/quicksort.c": [
            {"name": "swap", "start_line": 5, "end_line": 15, "sloc": 6},
            {"name": "partition", "start_line": 18, "end_line": 50, "sloc": 13},
            {"name": "quickSort", "start_line": 53, "end_line": 72, "sloc": 8},
        ]
    }, parsed
