# Benchmarks for Avocado Agent

Use [`verify_program.py`](./verify_program.py) to run CBMC on a benchmark program.

For example,
  to check the specified functions in `eval/benchmarks/csv_parser`,
  run

```sh
% ./eval/verify_program.py eval/benchmarks/csv_parser/
```

## Additional Evaluation Infrastructure

- [compare_specification_quality.py](./compare_specification_quality.py): Consumes two JSONL files
  (one for a baseline, the other for an experimental treatment) and produces a report showing the
  delta between kill scores and clause redundancy scores.
  - The JSONL files are produced by `eval/mutants/evaluate_specification_quality.py`, which this
    treatment (`parallel-avocado-minus-mutation-testing`) omits; run it from a checkout that has it.

## Sample Programs

- [`csv_parser`](./benchmarks/csv_parser/): Simple, minimal CSV parser.
  - [Source](https://github.com/semitrivial/csv_parser/tree/master)
- [`mkey`](./benchmarks/mkey/): Nintendo console master key generator
  - [Source](https://github.com/dazjo/mkey/tree/master)
- [`quicksort`](./benchmarks/quicksort/): Standard implementation of the Quicksort algorithm.
