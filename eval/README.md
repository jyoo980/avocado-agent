# Benchmarks for Avocado Agent

Use [`verify_program.py`](./verify_program.py) to run CBMC on a benchmark program.

For example,
  to check the specified functions in `eval/benchmarks/csv_parser`,
  run

```sh
% ./eval/verify_program.py eval/benchmarks/csv_parser/
```

## Additional Evaluation Infrastructure

- [mutate_function.py](./mutants/mutate_function.py): Generates a set of mutants for a C function,
    given a C file and a target function.
- [mutate_specification.py](./mutants/mutate_specification.py): Generates a set of mutants for a CBMC
    specification, given a C file and a target function.
  - It currently produces specification mutants in which clauses have been removed.
- [generate_mutants_and_compute_score.py](./mutants/generate_mutants_and_compute_score.py): Generates a set of mutants (via `mutate_function.py`) and runs mutation testing.
- [evaluate_specification_quality.py](./mutants/evaluate_specification_quality.py): Walks one or more
  C programs and emits a JSONL stream of evaluation metrics (i.e., kill scores, clause redundancy)
  for each function.
- [compare_specification_quality.py](./compare_specification_quality.py): Consumes two JSONL files
  (one for a baseline, the other for an experimental treatment) and produces a report showing the
  delta between kill scores and clause redundancy scores.
  - Use [evaluate_specification_quality.py](./mutants/evaluate_specification_quality.py) to generate
    the JSONL files for comparison.

## Sample Programs

- [`csv_parser`](./benchmarks/csv_parser/): Simple, minimal CSV parser.
  - [Source](https://github.com/semitrivial/csv_parser/tree/master)
- [`mkey`](./benchmarks/mkey/): Nintendo console master key generator
  - [Source](https://github.com/dazjo/mkey/tree/master)
- [`quicksort`](./benchmarks/quicksort/): Standard implementation of the Quicksort algorithm.
