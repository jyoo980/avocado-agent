# Measuring Avocado Agent

Scripts for measuring different variants of Avocado Agent: scoring
specifications, running agent experiments on the host, and summarizing and
comparing runs.

## Where the data goes

The host-side scripts write into `avocado-experimental-data/`
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
- `session_timeline.py --projects-dir <dir> --timeout <secs> <run-log>...`
  reads the Claude Code transcripts of the sessions that
  `<stem>-avocado-verify.jsonl` run logs name, and attributes each second of a
  session to model generation or to a tool call. It prints, for all the given
  run logs together, the split between model time and tool time (by kind of
  tool), and the sessions that cost the most; run it once per arm to compare
  arms. Pass `--timeout` the `CLAUDE_TIMEOUT` the run used (default 1800): it
  is charged to every session the harness killed.
- `compare_arms.py` with `--baseline`, `--treatment`, `--runs` and
  `--benchmarks` is the paired comparison that decides a treatment.
- `compare_on_intersection.py <baseline-jsonl> <treatment-jsonl>` compares two
  arms over only the functions both specified. Use it when a run was cut short,
  where pooling everything would reward whichever arm stopped earlier.
- `rescore_run.sh <label> <run-id> <benchmark>...` re-scores a run's
  specifications from one checkout. Use it whenever a scoring pass was
  interrupted, or to make sure both arms were scored by identical code.

## Traps

- `AVOCADO_SCORER_ROOT` must name a checkout with its own `.venv` (from
  `uv sync`): `run_agent_experiment.sh` runs the scorer from that checkout, so
  `uv` resolves the scorer's modules there. (Earlier versions ran it from the
  arm's checkout, mixing the scorer script with the arm's modules; that crashed
  two scoring passes.)
- Scoring uses the stubs saved with the run in
  `avocado-experimental-data/stubs/<label>/<run-id>/<benchmark>/`, not the
  scoring checkout's `stubs/`, so a rescore verifies against the same stubs the
  agent pass left. Runs saved before this was added have no saved stubs;
  `rescore_run.sh` warns and uses the checkout's `stubs/` for them.
- Do not run `make clean-mutants` from the repository root while an experiment
  is running: it deletes in-progress artifacts under `avocado-experimental-data/`.
- Claude Code's auto-memory gives each agent session a persistent notes
  directory, keyed by the repository's main worktree and therefore shared by
  every worktree of it. Two arms run on one machine from worktrees of one
  repository share those notes, so what one arm learns can carry over to the
  other, unless the experiment gives each arm its own notes directory.
