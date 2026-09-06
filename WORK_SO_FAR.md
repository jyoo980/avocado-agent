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

## Parallel evaluation, scratch directories, per-mutant feedback budget

Findings: "Parallelize evaluation across functions (and files)", "Raise the mutant worker cap",
"Shorter per-mutant CBMC budget for the agent-facing tool" in `FINDINGS.md`.

Commit: a0620ca. **Kept.**

### What changed and why

Harness (deterministic, measured with one run per tier, scores compared record-by-record):

- `eval/mutants/evaluate_specification_quality.py` gained `--jobs N` (default: CPU count). Every
  annotated function of every file is submitted to one thread pool up front; records are still
  written in the original file/function order so the JSONL is identical to a sequential run. The
  clause-redundancy metric is unchanged and still sequential.
- `tools/run_cbmc.py`: a process-wide `BoundedSemaphore` (`AVOCADO_MAX_CONCURRENT_CBMC`, default
  CPU count) gates every pipeline subprocess so nested fan-out (functions × mutants) cannot
  oversubscribe the machine and turn decidable mutants into timeouts. `run_cbmc` takes an
  optional `timeout_sec` (default unchanged, 600 s). Each subprocess record in
  `<stem>-cbmc-runs.jsonl` now carries its wall-clock `seconds`.
- `tools/util/mutation.py`: mutant files are named `<stem>__mutant_<function>_<i>.c` so functions
  of one file can be scored concurrently; the baseline verification runs in a private scratch
  directory (like mutants already did); the fixed 32-worker cap is gone (the semaphore bounds
  load instead); each `MutantVerificationResult` records `seconds`; per-mutant
  `*-cbmc-runs.jsonl` logs are deleted together with the mutant files.
- `tools/util/tree_sitter_utils.py` and `tools/construct_call_graph.py`: a lock around the shared
  tree-sitter parser and around the call-graph cache write, both now reached from several threads.
- `tools/run_cbmc_and_mutation_testing.py` (`avocado-run-cbmc`) no longer carries its own copy of
  the pipeline; it calls `tools.run_cbmc.run_cbmc` in a temporary directory (no `.goto` files in
  the agent's working directory; concurrent tool invocations cannot clobber each other) and
  verifies mutants with a 120 s budget (`_AGENT_MUTANT_TIMEOUT_SEC`). `avocado_verify.py`'s
  ground-truth re-verification uses the same scratch-directory helper and keeps the full 600 s.

Soundness: no CBMC check, `--unwind`, or `--depth` changed; the evaluation metric's mutant
generation, scoring, and 600 s timeout are untouched; a timed-out mutant is still undecided and
never killed, under either budget.

### Measurements

Command (worktree at the treatment commit, scoring the untouched committed benchmarks):

```sh
scripts/experiments/measure_quality.sh par-eval /app/eval/benchmarks/quicksort \
    /app/eval/benchmarks/csv_parser /app/eval/benchmarks/mkey /app/eval/benchmarks/kilo
```

| Benchmark | Baseline real / user / sys (s) | Treatment real / user / sys (s) | Scores |
| --- | ---: | ---: | --- |
| quicksort | 5.48 / 8.97 / 3.34 | 2.94 / 8.48 / 2.94 | identical (3 records) |
| csv_parser | 6.94 / 32.99 / 10.73 | 3.69 / 32.77 / 10.26 | identical (5 records) |
| mkey | 29.73 / 84.36 / 36.15 | 5.17 / 107.02 / 67.54 | identical (46 records) |
| kilo | 1103.17 / 619.97 / 123.40 | 414.40 / 1125.25 / 206.50 | identical (35 records) |

"Identical" means every `mutation_summary` record (killed, survived, timed out, compile failed,
kill score) is equal to the baseline's; the only textual difference is that the treatment was
invoked with absolute benchmark paths, which appear verbatim in the free-text `metadata` of
records that were not mutation tested. JSONL files: `avocado-experimental-data/baseline-*.jsonl`
vs `avocado-experimental-data/par-eval-*.jsonl`.

Agent time: the 120 s mutant budget only affects agent sessions; it is measured together with the
other agent-facing changes in the "Treatment T1" agent entry below (3 runs vs. 3 baseline runs).

## Treatment T2: workflow guidance and a per-function prompt

Findings: "Give the inner agent a richer per-function prompt", "Guide callee contracts away from
`__CPROVER_is_fresh` on possibly-aliasing pointers", "Tell the agent what kind of postcondition
kills mutants" in `FINDINGS.md`.

Commit: T2_COMMIT_PLACEHOLDER. Status: **measurement pending** (to be run after the T1 agent
runs; see the "Agent measurements" entry below).

### What changed and why

- `CLAUDE.md` gained two short sections. "Workflow" tells the agent to read callee contracts and
  callers first, run `avocado-run-cbmc` immediately (never CBMC by hand, never in the background,
  never the harness's JSONL logs), and stop once every decided mutant is killed or two consecutive
  runs leave the score unchanged. "Writing contracts that verify and kill mutants" explains the
  `is_fresh`-vs-`w_ok`/`r_ok` trap at call sites, that exact-value postconditions kill mutants
  where bounds do not, that bounded sizes keep mutants decidable, and what `__CPROVER_assigns`
  must cover.
- `avocado_verify.py` builds a per-function prompt (`_build_prompt`) that includes the exact
  `avocado-run-cbmc` command with the include directories the harness detected (previously the
  agent had to guess `-I` flags), the in-file callees whose contracts replace their bodies, and
  the in-file callers whose call sites must satisfy the new preconditions.

Nothing about CBMC's checks or the metric changed.
