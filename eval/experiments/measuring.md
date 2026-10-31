# Measuring Avocado Agent

Scripts for measuring Avocado Agent while changing it.

## Where the data goes

By default, everything the scripts write lands in `avocado-experimental-data/`
at the repository root, which **`.gitignore` excludes**. Every JSONL and `.time`
file they produce is therefore local to the machine that produced it: a fresh
clone has the scripts but none of the data. To re-derive the data, re-run the
scripts.

## Deterministic measurement, no agent involved

- `measure_quality.sh <label> <benchmark-dir>...` scores the specifications a
  benchmark already carries and records wall/user/sys time. Use it for any
  change that cannot alter a score, and to prove that it did not.
- `summarize_quality.py <jsonl>...` prints per-function kill scores plus the
  mean and pooled aggregates, and the spread across several files.
