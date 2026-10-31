# Measuring Avocado Agent

Scripts for measuring Avocado Agent while changing it: scoring specifications,
running agent experiments, and summarizing and comparing runs.

## Which launcher to use

Two kinds of script in this directory start agent runs:

- `run.sh` runs each run in a fresh container, so no run can see another's
  specifications, session transcripts or memory. Use it when runs must not
  influence one another, as for numbers to report. Its [README](README.md)
  documents it. It runs one treatment per batch and does not score the
  specifications; see [Scoring `run.sh` output](#scoring-runsh-output).
- `run_agent_experiment.sh` and `run_paired_batches.sh` run `avocado-verify`
  on the host, from a local checkout, and score the result. They are quicker to
  set up and suit iterating on a change. Only their output has the layout that
  `compare_arms.py` and `rescore_run.sh` read, so only they support the paired
  comparison that decides a treatment. Their runs are not isolated: runs from
  one checkout, or from worktrees of one repository, share Claude Code's
  auto-memory (see the last item under [Traps](#traps)).

### Scoring `run.sh` output

A `run.sh` batch leaves each run in a directory
`<results>/<tag>/<timestamp>/run-<i>/`, whose `subject/` holds the specified
program (see [Step 4 of the README](README.md#step-4-read-the-results)).

- To score a run, pass its `subject/` directory to `measure_quality.sh`, with a
  label that names the run, such as `<tag>-run-1`. The scores land in
  `avocado-experimental-data/<label>-subject.jsonl`, which
  `summarize_quality.py` and `compare_on_intersection.py` read.
  `measure_quality.sh` deletes CBMC build artifacts and mutants from the
  directory that it scores, so score a copy to keep them.
- With the `avocado-verify` runner, `progress.sh` and `summarize_agent_runs.py`
  read agent time and cost from `run-<i>/` directories. The `claude` runner
  writes no per-function logs; a run's cost is in its `claude-output.json`.
- `compare_arms.py` and `rescore_run.sh` do not read `run.sh` output.
- `run.sh` does not pair two treatments as `run_paired_batches.sh` does, and a
  run's wall-clock time depends heavily on how much CBMC work competes with it.
  To compare the times of two treatments, launch their batches at the same time
  with the same `-n` and `-j`, and expect a noisier comparison than a paired
  one.

## Where the data goes

By default, everything the host-side scripts write lands in
`avocado-experimental-data/` at the repository root, which **`.gitignore`
excludes**. Every JSONL and `.time` file they produce is therefore local to the
machine that produced it: a fresh clone has the scripts but none of the data.
To re-derive the data, re-run the scripts. Setting `AVOCADO_DATA_DIR` sends the
output elsewhere; `.gitignore` does not cover such a directory, so keep it out
of version control yourself.

## Deterministic measurement, no agent involved

- `measure_quality.sh <label> <benchmark-dir>...` scores the specifications a
  benchmark already carries and records wall/user/sys time. Use it for any
  change that cannot alter a score, and to prove that it did not.
- `summarize_quality.py <jsonl>...` prints per-function kill scores plus the
  mean and pooled aggregates, and the spread across several files.
- `compare_on_intersection.py <baseline-jsonl> <treatment-jsonl>` compares two
  arms over only the functions both specified. Use it when a run was cut short,
  where pooling everything would reward whichever arm stopped earlier.

## Agent measurement

- `strip_specs.py <src> <dest>` copies a benchmark with every contract clause
  removed. Agent runs start from such a copy so the run measures what the
  system produces, and the checked-in benchmarks are never modified.
- `run_agent_experiment.sh <label> <run-id> <benchmark-dir>...` runs one arm:
  strip, `avocado-verify` over each file, then score. Its header documents the
  environment variables, including `AVOCADO_SKIP_GLOB` for vendored sources.
- `run_paired_batches.sh` runs two arms concurrently, one batch at a time, so
  both see the same machine load.
- `progress.sh <run-dir>...` prints per-function outcome, turns, agent seconds
  and cost for a run in flight.
- `summarize_agent_runs.py <run-dir>...` aggregates a finished run, charging a
  session killed by the harness timeout its full timeout rather than zero.
- `compare_arms.py` with `--baseline`, `--treatment`, `--runs` and
  `--benchmarks` is the paired comparison that decides a treatment.
- `rescore_run.sh <label> <run-id> <benchmark>...` re-scores a run's
  specifications from one checkout. Use it whenever a scoring pass was
  interrupted, or to make sure both arms were scored by identical code.

## Traps

- `AVOCADO_SCORER_ROOT` changes which *script* scores a run, but `uv` still
  resolves the *modules* from the arm's own checkout. Mixing them crashed two
  scoring passes. To score arms identically, run `rescore_run.sh` from a single
  checkout afterwards.
- Do not run `make clean-mutants` from the repository root while an experiment
  is running: it deletes in-progress artifacts under `avocado-experimental-data/`.
- Claude Code's auto-memory gives each agent session a persistent notes
  directory, keyed by the repository's main worktree and therefore shared by
  every worktree of it. Two arms run on one machine from worktrees of one
  repository share those notes, so what one arm learns can carry over to the
  other, unless the experiment gives each arm its own notes directory.
