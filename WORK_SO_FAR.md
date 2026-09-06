# Work So Far: Improving Avocado Agent

All numbers below were produced on the `self-improvement` branch. Commands are given relative to the
repository root (`/app`). Experiment artifacts (JSONL score files, `time` logs, agent run
directories) live under `avocado-experimental-data/`.

## Step 0: Environment verification (2026-09-06)

Measured at commit `94a0f38` (untouched tree).

- **Not inside the Docker container.** The environment is a bare Ubuntu 24.04 host (128 CPUs,
  503 GB RAM) with the same toolchain the Dockerfile installs: CBMC 6.9.0 (`cbmc`, `goto-cc`,
  `goto-instrument` on `PATH`), `uv`, and the Claude Code CLI. `git` and `prek` were missing and
  were installed (`apt-get install git`, `uv tool install prek`); neither is part of the system
  under evaluation.
- `avocado-run-cbmc --function swap --file eval/benchmarks/quicksort/quicksort.c` succeeds
  (`swap verified successfully`, no mutable operators) in 0.5 s.
- `claude -p` is authenticated: a trivial prompt with `--output-format json` completes in ~8 s and
  reports a cost. The inner agent's model is configured in `.claude/settings.json`
  (`claude-fable-5-1`, effort `high`). Agent-time experiments are therefore possible.
- `make test`: 52 passed. `make checks` (`prek -q run --all-files`): clean.

## Baseline (commit `94a0f38`)

### Deterministic part: committed specifications

The benchmark programs are checked in *with* CBMC contracts from earlier Avocado runs, so
`evaluate_specification_quality.py` on the untouched tree scores those committed specifications.
This is the reference for every harness-only change (which must reproduce it exactly) and the
harness-time reference.

Command (one invocation per benchmark, sequential; wraps
`./eval/mutants/evaluate_specification_quality.py <BENCHMARK_DIR> --auto-include --mutation --jsonl ...`
in `time -p`):

```sh
scripts/experiments/measure_quality.sh baseline eval/benchmarks/quicksort eval/benchmarks/csv_parser \
    eval/benchmarks/mkey eval/benchmarks/kilo
scripts/experiments/summarize_quality.py avocado-experimental-data/baseline-*.jsonl
```

Outputs: `avocado-experimental-data/baseline-<benchmark>.jsonl` (scores) and
`avocado-experimental-data/baseline-<benchmark>.time` (log plus `real`/`user`/`sys`).

| Benchmark | Annotated functions | Mutation tested | Did not verify | No mutants | Killed/decided | Pooled kill score | Mean kill score | real (s) | user (s) | sys (s) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| quicksort | 3 | 1 | 1 | 1 | 5/7 | 0.7143 | 0.7143 | 5.48 | 8.97 | 3.34 |
| csv_parser | 5 | 2 | 2 | 1 | 0/24 | 0.0000 | 0.0000 | 6.94 | 32.99 | 10.73 |
| mkey | 46 | 14 | 5 | 27 | 17/153 | 0.1111 | 0.1937 | 29.73 | 84.36 | 36.15 |
| kilo | 35 | 7 | 20 | 8 | 26/129 | 0.2016 | 0.2332 | 1103.17 | 619.97 | 123.40 |

"Pooled kill score" is killed / decided mutants over the whole benchmark; "mean kill score" is the
arithmetic mean of the per-function `kill_score` values, which is what
`eval/compare_specification_quality.py` differences. Timed-out and compile-failed mutants are
excluded from "decided" by the (unchanged) scorer. Per-function scores are in the JSONL files.

Notes:

- `lz4_lib/` carries no CBMC annotations, so `evaluate_specification_quality.py` has nothing to
  score there; its deterministic baseline is empty and it is only meaningful for agent runs.
- Kilo's 1103 s is dominated by a few committed specs whose baseline verification or mutants hit
  the 600 s CBMC timeout, run back-to-back (e.g. `editorDelRow`).
- The committed `partition` spec in quicksort fails to verify because the committed `swap` spec
  requires `__CPROVER_is_fresh` on both pointers, which the call `swap(&arr[i], &arr[j])` cannot
  satisfy under contract replacement. This is recorded in `FINDINGS.md`.

### Agent part: `avocado-verify` from a spec-less start

Agent runs start from a copy of each benchmark with every contract clause removed
(`scripts/experiments/strip_specs.py`; the committed benchmark files are never modified) so the
measurement reflects what the system produces on its own rather than the committed specs. Each run
copies the benchmark to `avocado-experimental-data/runs/<label>/<run-id>/<benchmark>/`, runs
`avocado-verify --file` on every `.c` file, then scores the result with
`evaluate_specification_quality.py --mutation`.

```sh
scripts/experiments/run_agent_experiment.sh baseline <run-id> eval/benchmarks/quicksort eval/benchmarks/csv_parser
scripts/experiments/summarize_agent_runs.py avocado-experimental-data/runs/baseline/{1,2,3}
scripts/experiments/summarize_quality.py avocado-experimental-data/baseline-{1,2,3}-quicksort.jsonl
scripts/experiments/summarize_quality.py avocado-experimental-data/baseline-{1,2,3}-csv_parser.jsonl
```

Three independent runs over the iteration tier are in progress at the time of this entry; their
numbers are recorded in the "Agent baseline" entry below once complete.
