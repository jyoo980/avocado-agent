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

The agent baseline is measured together with each treatment, in paired batches: one baseline run
and one treatment run start at the same moment on the same machine, and the next pair starts only
when both finish. Pairing matters because a run's wall-clock depends heavily on how much CBMC work
is competing with it. The numbers are in the treatment entries below.

An earlier attempt ran nine agent runs concurrently (labels `baseline`, `t1`, `t2`, run ids 1-3).
All nine hit the account's usage limit part-way through `csv_parser`, so every arm stopped at a
different function. Those results are kept in `avocado-experimental-data/` for reference but are
**not** used for any conclusion: an arm that stops earlier specifies fewer functions, and the mean
kill score is taken over specified functions only, so truncated runs are not comparable. Every
number below comes from the paired batches (run ids 4-9), which completed in full.

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

Agent time: the 120 s mutant budget only affects agent sessions, so it is measured together with
the prompt changes in the combined agent measurement below.

## Treatment T2: workflow guidance and a per-function prompt

Findings: "Give the inner agent a richer per-function prompt", "Guide callee contracts away from
`__CPROVER_is_fresh` on possibly-aliasing pointers", "Tell the agent what kind of postcondition
kills mutants" in `FINDINGS.md`.

Commits: cc8f99d and 51e6e98. **Kept** (see the agent measurement below).

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

## Agent measurement: baseline (94a0f38) versus the kept changes (51e6e98)

This is the measurement that decides whether everything above is kept. It compares the untouched
harness against the harness with both the parallel-evaluation change and the prompt/`CLAUDE.md`
change, over three paired runs on the iteration tier (quicksort and csv_parser, eight functions).

Both arms start from a copy of the benchmarks with every contract clause removed, so each run
writes its specifications from scratch; the committed benchmark files are never touched.

```sh
# One paired batch per run id: a `base` run (94a0f38) and a `final` run (51e6e98) start together.
/root/avocado-runner/batches.sh 4 5 6          # wraps scripts/experiments/run_agent_experiment.sh
scripts/experiments/compare_arms.py --baseline base --treatment final --runs 4 5 6 \
    --benchmarks quicksort csv_parser
```

| Metric (mean over 3 runs [min..max]) | base (94a0f38) | final (51e6e98) |
| --- | --- | --- |
| mean kill score | 0.500 [0.500..0.500] | 0.500 [0.500..0.500] |
| pooled kill score | 0.402 [0.400..0.407] | 0.400 [0.400..0.400] |
| mutants killed / decided | 22 / 54.7 | 22 / 55.0 |
| functions verified (of 8) | 8 | 8 |
| agent time (s, inside `claude -p`) | 2070.9 [265.9..5679.4] | 580.8 [245.4..1151.2] |
| agent-pass wall-clock (s) | 2100.7 [297.0..5707.2] | 611.4 [277.0..1180.4] |
| sessions killed by the 1800 s timeout | 1.0 [0..3] | 0 [0..0] |
| reported cost (USD) | 3.48 [3.17..3.67] | 5.51 [3.29..9.11] |

**Quality is unchanged.** The mean kill score is 0.5000 in all six runs. The pooled score differs
by 0.002 in one run (one extra decided mutant), far inside the run-to-run spread. Both arms verify
all eight functions in every run. Per the tie-break rule this clears the quality floor.

**Agent time falls by 3.6x on the mean**, but the mean hides the shape of the win: two of the three
paired runs are a wash (+80 s and -22 s), and the whole difference comes from run 4, where the
baseline spent 5679 s against the treatment's 1151 s. In that run three baseline sessions were
killed by the harness's 1800 s per-function timeout: one on `fread_csv_line` and two of the three
allowed sessions on `split_on_unescaped_newlines`, which alone consumed about 90 minutes. The
transcripts show why. The baseline agent runs CBMC by hand, backgrounds the tool, reads the
harness's Python sources, and in one session downloaded CBMC's own C++ source from GitHub to study
the inliner. The treatment's `CLAUDE.md` forbids exactly that, and no treatment session was killed
by the timeout in any run. So the change does not make a typical session faster; it removes the
tail where a session burns its entire budget without ever running the verifier.

**Cost.** The recorded costs are not directly comparable: a session killed by the timeout reports
neither a duration nor a cost, so the baseline's $3.48 excludes its three killed sessions
altogether (the comparison script charges them 1800 s of time but cannot invent a price). The
treatment's higher recorded cost is real but is measured against an unknown, larger baseline.

**Threats to validity.**

- `CLAUDE.md` lets the agent add stub files to the harness checkout, and `build_stub_index` then
  applies them to every later verification in that checkout. During these runs the baseline arm's
  checkout accumulated `stubs/stdlib.c` and `stubs/string.c`, so its runs 5 and 6 were not
  independent of run 4. `run_agent_experiment.sh` now resets `stubs/` and `scripts/` before each
  run (commit a39214c); that reset was added after these runs and is not reflected in them.
- Scoring for the `base` arm runs the arm's own checkout (the venv resolves the project at the
  checkout root regardless of which copy of the script is invoked), so the two arms are scored by
  different code. That code produces identical scores by construction -- the parallel scorer was
  validated record-for-record against the sequential one on all four benchmark tiers -- but it is
  a difference worth naming.
- One scoring pass (`base` run 6, csv_parser) crashed because a `make clean-mutants` run in this
  repository deleted its in-flight artifacts. It was re-scored from the specifications the run left
  on disk with `scripts/experiments/rescore_run.sh base 6 csv_parser`; the table uses that result.

## Treatment T3: prefer total postconditions over guarded ones (reverted)

Finding: "Guarded postconditions kill nothing; prefer total ones over a narrower precondition".

Commit: fcba9af, reverted by 00c1264. **Reverted.**

`CLAUDE.md` gained a paragraph telling the agent that a postcondition guarded by a narrow
antecedent is vacuous on almost every input the precondition allows, and that narrowing the
precondition is the way to make a clause bite. It was measured over three paired runs against the
kept treatment:

```sh
/root/avocado-runner/batches_t3.sh 7 8 9
scripts/experiments/compare_arms.py --baseline final --treatment t3 --runs 7 8 9 \
    --benchmarks quicksort csv_parser
```

| Metric (mean over 3 runs [min..max]) | final | t3 |
| --- | --- | --- |
| mean kill score | 0.500 [0.500..0.500] | 0.500 [0.500..0.500] |
| pooled kill score | 0.400 [0.400..0.400] | 0.400 [0.400..0.400] |
| functions verified (of 8) | 8 | 8 |
| agent time (s) | 513.3 [249.0..1007.0] | 508.3 [267.7..965.8] |
| reported cost (USD) | 5.14 [3.59..8.07] | 5.44 [3.72..8.42] |

Every run of both arms produced exactly 22 killed of 55 decided mutants. The per-run agent-time
deltas were -41 s, +7 s and +19 s, which is noise. The paragraph was reverted rather than kept: it
lengthens the prompt for no measured gain. The reason it cannot help is recorded as its own
finding -- the two functions that score zero do so because their bodies are driven by unstubbed
libc calls that CBMC treats as nondeterministic, not because of how the postconditions are shaped.

## Deterministic re-measurement at the final commit

The parallel-evaluation entry above was measured at commit a0620ca. Later commits touch the
inner-agent prompt, `CLAUDE.md`, and the experiment scripts, none of which the scorer runs, so the
deterministic numbers were re-taken at the final commit to confirm that:

```sh
scripts/experiments/measure_quality.sh head eval/benchmarks/quicksort eval/benchmarks/csv_parser \
    eval/benchmarks/mkey
```

Kilo was re-taken the same way. Every `mutation_summary` record matches the baseline exactly: 3 of
3 records on quicksort, 5 of 5 on csv_parser, 46 of 46 on mkey, 35 of 35 on kilo, zero score
differences anywhere. Wall-clock was 2.96 s, 3.66 s, 7.23 s and 604.26 s; all four ran while two
agent sessions were competing for the machine, which is why mkey and kilo sit above the 5.17 s and
414.40 s measured on an idle machine. Files: `avocado-experimental-data/head-*.jsonl`.

## Confirmation tier: paired agent runs on mkey

The iteration tier cannot show a quality difference (its kill score is pinned by two libc-heavy
csv_parser functions, see `FINDINGS.md`), so the kept changes were re-measured on mkey, where the
committed specifications leave plenty of headroom. mkey vendors polarssl, whose 77 functions carry
no committed specifications and would dominate the run, so those sources are excluded with
`AVOCADO_SKIP_GLOB='*/polarssl/*'`; the remaining four files hold the 49 functions the deterministic
mkey baseline scores.

```sh
AVOCADO_SKIP_GLOB='*/polarssl/*' AVOCADO_SCORER_ROOT=/app \
  scripts/experiments/run_agent_experiment.sh <base|final> <run-id> eval/benchmarks/mkey
scripts/experiments/compare_arms.py --baseline base --treatment final --runs <ids> --benchmarks mkey
```

Three paired runs were attempted (ids 14, 15, 16). Only run 14 completed: the account's usage
limit truncated run 15 near its end (the baseline arm reached 43 of 49 functions, the treatment 47)
and stopped run 16 after four functions, which is discarded. Run 15 is reported over the 42
functions both arms specified, using `scripts/experiments/compare_on_intersection.py`, so the two
arms are compared like for like.

Run 14, complete, all 49 functions in both arms:

| Metric | base (94a0f38) | final (51e6e98) |
| --- | ---: | ---: |
| mean kill score | 0.3024 | 0.3576 |
| pooled kill score | 0.2396 (52/217) | 0.2490 (62/249) |
| functions with a scorable contract | 17 | 21 |
| functions whose contract does not verify | 5 | 1 |
| functions verified by the harness (of 49) | 49 | 49 |
| sessions killed by the 1800 s timeout | 0 | 0 |
| agent time (s) | 4016.7 | 3223.0 |
| reported cost (USD) | 34.37 | 31.90 |

Run 15, restricted to the 42 functions both arms specified (22 of which have mutants in both):

| Metric | base | final |
| --- | ---: | ---: |
| mean kill score | 0.2576 | 0.2922 |
| pooled kill score | 0.2417 (51/211) | 0.2218 (55/248) |

Both runs move the mean kill score up, by 0.055 and 0.035. The pooled score moves up in run 14 and
down slightly in run 15, because the treatment decides more mutants (248 against 211 on the same
functions) and a larger denominator can lower a ratio even as the count of killed mutants rises.
Two functions account for most of the difference in both runs: `main_set_data_path`, where the
baseline writes a contract that does not verify and the treatment reaches 0.7143, and
`ctr_crypt_counter`, where the baseline scores 0.0000 against the treatment's 0.5000.

With one complete paired run and one partial one, this is weaker evidence than the three complete
paired runs on the iteration tier. It is enough to say the change does not lower the kill score on
the confirmation tier -- the requirement the tie-break rule imposes -- and that the direction is
favourable on both the mean and the count of functions carrying a contract worth scoring.

Both arms verify all 49 functions and annotate all 49; neither loses a session to the harness
timeout. The difference is in what the contracts are worth. The treatment writes contracts that
verify *and* have mutants to kill on 21 functions against the baseline's 17, and leaves only one
function with a contract that does not verify against the baseline's five. On the pooled measure
(every decided mutant in the benchmark, which cannot be gamed by specifying fewer functions) it
kills 62 of 249 against 52 of 217.

For scale, the committed specifications in `eval/benchmarks/mkey` score a mean of 0.1937 and a
pooled 0.1111 (17 of 153 decided mutants, 14 functions tested). Both agent arms beat the checked-in
specifications comfortably; the treatment beats the baseline arm.

**Measurement artifact worth recording.** The first scoring pass for the `base` arm of runs 14 and 15
crashed with a `JSONDecodeError` reading a half-written call-graph JSON. The cause was in the
experiment harness, not in either arm: `AVOCADO_SCORER_ROOT=/app` makes the *script* come from this
checkout while `uv` still resolves the *modules* from the arm's checkout, so the baseline arm ran
this checkout's parallel evaluation driver against its own unlocked `construct_call_graph`. That is
precisely the race the `_CALL_GRAPH_LOCK` in commit a0620ca removes, so it is evidence for that
change rather than against the baseline. Both runs were re-scored entirely within this checkout
using `scripts/experiments/rescore_run.sh base <id> mkey`, and the tables use those results.
