# Benchmarks for Avocado Agent

Use [`verify_program.py`](./verify_program.py) to run CBMC on a benchmark program.

For example,
  to check the specified functions in `eval/benchmarks/csv_parser`,
  run

```sh
% ./eval/verify_program.py eval/benchmarks/csv_parser/
```

## Additional Evaluation Infrastructure

- [mutate.py](./mutants/mutate.py): Generates a set of mutants given a C file and a target function.

## Sample Programs

- [`csv_parser`](./benchmarks/csv_parser/): Simple, minimal CSV parser.
  - [Source](https://github.com/semitrivial/csv_parser/tree/master)
- [`mkey`](./benchmarks/mkey/): Nintendo console master key generator
  - [Source](https://github.com/dazjo/mkey/tree/master)
- [`quicksort`](./benchmarks/quicksort/): Standard implementation of the Quicksort algorithm.
