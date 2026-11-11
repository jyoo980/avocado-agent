# Measuring Avocado Agent

Scripts for measuring different variants of Avocado Agent.

## Where the data goes

The scripts write into `avocado-experimental-data/`
at the repository root, which **`.gitignore` excludes**.
A fresh
clone has the scripts but none of the data. To re-derive the data, re-run the
scripts. Setting `AVOCADO_DATA_DIR` sends the output elsewhere.

## Deterministic measurement, no agent involved

- `measure_quality.sh <label> <benchmark-dir>...` reports mutation kill
  scores for scores the specifications a
  benchmark already carries and records wall/user/sys time. Use it to test
  an Avocado
  change that cannot alter a score, and to prove that it did not.
- `summarize_quality.py <jsonl>...` prints per-function kill scores plus the
  mean and pooled aggregates, and their mean, spread, and standard deviation
  across several files.
