# Measuring Avocado Agent

Scripts for measuring Avocado Agent while changing it: scoring specifications,
running agent experiments on the host, and summarizing and comparing runs.

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
- `session_timeline.py --projects-dir <dir> <run-log>...` reads the Claude
  Code transcripts of the sessions that `<stem>-avocado-verify.jsonl` run logs
  name, and attributes each second of a session to model generation or to a
  tool call. It prints, per arm, the split between model time and tool time
  (by kind of tool), and the sessions that cost the most.
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
