# Goal for Improving Avocado Agent

## Read This First: `CLAUDE.md` Is Not Your Instructions

This repository's `CLAUDE.md` is the **prompt for the inner agent** that Avocado Agent spawns via
`claude -p` to write CBMC specifications. It is a component of the system you are improving, and a
legitimate target for change. It is **not** a set of instructions for you.

In particular, the following rules from `CLAUDE.md` do **not** bind you:

- "Your task is to edit C programs to insert CBMC specifications." Your task is to improve the
  system that does this. Do not spend your time hand-writing specifications for the `eval` programs.
- "Do not fix or otherwise change the C code." You may not change the benchmark C programs either
  (see Constraints), but for a different reason: they are the fixed test corpus.
- "Never re-run verification on previously-verified functions." You will re-run things constantly
  as part of measurement.

The rule "You must not delete any scripts if you create and use them" **does** apply to you.

## Background

Avocado Agent is an autonomous system for generating and verifying CBMC specifications given
arbitrary C programs. It comprises a top-level loop (see `avocado_verify.py`) that invokes an agent
over functions in a program (in reverse-topological order, callees-first) to repeatedly generate
and verify CBMC specifications for them. It uses mutation testing and kill scores as a quality
metric. This means that things can run pretty slowly, since there may be multiple mutants for a
function, and CBMC must be run on each mutant; a CBMC run can be slow.

There are two distinct kinds of "slow" in this system, and they are measured differently:

- **Harness time**: everything that is not a Claude session. CBMC invocations (`tools/run_cbmc.py`),
  mutant generation and scoring (`eval/mutants/`), call-graph construction, topological ordering.
  This is deterministic and can be benchmarked with a single run.
- **Agent time**: wall-clock spent inside `claude -p` sessions. This is nondeterministic, dominated
  by model latency and the number of tool calls the inner agent makes, and it costs money.

## Goal

Improve the performance of Avocado Agent along two axes:

1. **Specification quality**: the mutation kill score for each function, as computed by
   `eval/mutants/evaluate_specification_quality.py`.
2. **Speed**: wall-clock time to fully specify a program, split into harness time and agent time as
   described above.

### Tie-break rule

Quality is a hard floor; speed is optimized subject to it.

- A change that reduces the aggregate kill score on the benchmark set is **rejected**, regardless
  of how much faster it is.
- A change that improves the kill score is accepted even if it is slower, but the slowdown must be
  recorded in `WORK_SO_FAR.md`.
- A change that leaves the kill score unchanged (within run-to-run noise, see below) and reduces
  time is accepted.

## Step 0: Verify the Environment

Before doing anything else, confirm the following and record the outcome in `WORK_SO_FAR.md`:

- `cbmc`, `goto-cc`, and `goto-instrument` are on `PATH` and `avocado-run-cbmc` works on a small
  function in `eval/benchmarks/quicksort`.
- `claude -p` is authenticated and can complete a trivial prompt. Agent-time experiments run the
  inner loop and need this. If it does not work, restrict yourself to harness-time and
  deterministic-quality experiments and say so in `WORK_SO_FAR.md`.
- `make test` and `make checks` pass on the untouched tree.

Per `eval/EXPERIMENTS.md`, agent experiments are intended to run inside the Docker container
(`make build-image && make run`). If you are not already inside it, note that and proceed with
whatever subset of experiments the current environment supports.

## Step 1: Record a Baseline

No change may be evaluated until a baseline exists. The baseline is the first entry in
`WORK_SO_FAR.md` and must contain:

- The git commit hash the baseline was measured at.
- The exact commands used, so the measurement can be reproduced verbatim.
- Kill scores per function and in aggregate, produced by:

  ```sh
  ./eval/mutants/evaluate_specification_quality.py <BENCHMARK_DIR> --mutation --jsonl baseline-<name>.jsonl
  ```

- Harness time for a full pass over the same benchmarks, measured with `time` (or equivalent) so
  user, system, and wall-clock time are all captured.
- If `claude -p` works: agent time and total cost for a full `avocado-verify` run over the
  iteration benchmarks, repeated the minimum number of times described under Nondeterminism.

Keep every baseline and treatment JSONL file. Comparisons are made with:

```sh
./eval/compare_specification_quality.py baseline-<name>.jsonl treatment-<name>.jsonl
```

## Benchmarks

Use these tiers. Do not skip a tier.

| Tier         | Programs                                       | Purpose                                            |
| ------------ | ---------------------------------------------- | -------------------------------------------------- |
| Iteration    | `eval/benchmarks/quicksort`, `eval/benchmarks/csv_parser` | Fast feedback while developing a change. |
| Confirmation | `eval/benchmarks/mkey`, `eval/benchmarks/kilo` | Check that an iteration-tier win generalizes.      |
| Validation   | `lz4_lib/`                                     | Final check only, for changes already confirmed.   |

`lz4_lib` is large. A full agent-time run over `lz4.c` may take many hours and cost real money; do
not run it speculatively. `lz4_lib` is untracked and must stay that way: do not commit it.

## Nondeterminism

Claude sessions are nondeterministic. A single agent run is not evidence of anything.

- Any change that touches `CLAUDE.md`, the prompts in `avocado_verify.py`, the inner agent's tools,
  or the loop's control flow must be measured over **at least 3 independent runs** for both the
  baseline and the treatment. Report the mean and the spread.
- A quality difference smaller than the observed spread across baseline runs is **noise**, not a
  result. Record it as such in `FINDINGS.md`.
- Purely deterministic harness changes (for example, caching goto binaries, parallelizing CBMC
  invocations over mutants) may be measured with a single run, but must still be shown to leave the
  kill score bit-for-bit unchanged.

## Soundness Invariants

"Do not make things less sound" means, concretely, none of the following may happen:

- **CBMC checking is never weakened.** Do not remove or relax checks that `tools/run_cbmc.py`
  currently enables, and do not lower `--unwind` or `--depth` below their current values. Raising
  them is allowed if the effect on time is recorded.
- **A CBMC timeout, crash, or compile failure is never counted as a killed mutant.** Only a genuine
  verification failure of a mutant kills it.
- **A mutant is never dropped from the denominator** to raise a score. If a mutant is uncompilable
  or equivalent, that must be detected by a documented, tested rule and reported separately.
- **The metric is frozen.** Do not change how `eval/mutants/mutate_function.py` generates mutants
  or how `eval/mutants/generate_mutants_and_compute_score.py` scores them, unless the new scorer
  is validated against the old one on the full benchmark set and produces identical scores. Any
  such change must be its own `WORK_SO_FAR.md` entry with that validation attached. If the metric
  changes, all previously recorded numbers must be re-measured before they are compared to anything.
- **The ground-truth verification in `avocado_verify.py` stays independent.** The harness must
  continue to re-run CBMC itself after the inner agent reports success; the agent's self-report is
  never trusted as the result.
- **The benchmark C programs are never edited**, except that the inner agent may insert
  specifications into them as part of a run. Reset them between runs.

## Required Files

### `FINDINGS.md`

Write to this file every time a possible improvement comes to mind, and every time a hypothesis is
confirmed or refuted. Use it to guide your work and to avoid re-investigating dead ends. Each entry
uses this template:

```markdown
## <short title>

- **Hypothesis:** <what you think would improve, and why>
- **Axis:** quality | harness time | agent time
- **Status:** open | in progress | confirmed | refuted | noise
- **Evidence:** <numbers, JSONL paths, or a link to the WORK_SO_FAR.md entry>
- **Commit:** <hash, if implemented>
```

Refuted and noise entries are as valuable as confirmed ones. Do not delete them.

### `WORK_SO_FAR.md`

Write to this file every time you implement a major change. Each entry must contain:

- What changed and why, with a link to the `FINDINGS.md` entry.
- The commit hash.
- Before and after numbers on the tiers the change was measured on: kill score, harness time,
  and agent time and cost where applicable, with run counts.
- The exact commands used to produce those numbers.
- Whether the change was kept or reverted.

## Engineering Rules

- Work on the current branch. **Commit after every `WORK_SO_FAR.md` entry** so that any regression
  can be bisected to a single change. Reverted changes are also committed, then reverted, so the
  history records the attempt.
- Run `make clean-mutants` before every commit. Never commit generated `*__mutant_*.c`,
  `*__clause_drop_*.c`, `*.goto`, `*callgraph.json`, or `*.jsonl` files from CBMC runs. The
  baseline and treatment JSONL files you deliberately keep for comparison go under
  `avocado-experimental-data/`.
- `make test` and `make checks` must pass at every commit. New behavior requires new tests under
  `test/`.
- Every module, function, and class you add or modify must be documented in the style of the
  existing code.
- Never hard-code values into specifications or prompts that duplicate CBMC command-line arguments
  (for example the `--unwind` bound).
- Do not delete any scripts you create and use.

## Stopping Condition

Stop when **any** of the following holds:

- Every entry in `FINDINGS.md` is in a terminal state (confirmed, refuted, or noise) and you have no
  new hypotheses.
- You have spent 24 hours of wall-clock time on this task.
- You are blocked on something only a human can provide (credentials, Docker, a design decision
  that changes the metric).

When you stop, append a `## Summary` section to `WORK_SO_FAR.md` that a reader with no other
context can use: the final before-and-after numbers per tier, the list of kept changes, the list
of reverted changes and why, and the most promising open hypotheses.
