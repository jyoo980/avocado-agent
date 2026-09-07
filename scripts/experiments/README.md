# Experiment tooling

Scripts used to measure Avocado Agent while changing it. Results and analysis
live in `../../WORK_SO_FAR.md`; the hypotheses behind them live in
`../../FINDINGS.md`.

## Where the data goes, and why you may not have it

Everything these scripts write lands in `avocado-experimental-data/` at the
repository root, which **`.gitignore` excludes**. That is the repository's own
convention and it predates this work, but it has a consequence worth stating
plainly: every JSONL and `.time` file cited in `WORK_SO_FAR.md` and
`FINDINGS.md` is local to the machine that produced it. A fresh clone has the
documents and the scripts but none of the evidence. To re-derive it, re-run the
commands quoted in those documents.

The same applies to the branches those documents mention (`addlib`,
`depth2000`, `t3`), which record rejected treatments. They were never pushed,
so they exist only where the work was done.

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
  both see the same machine load. Its header lists the exact invocations behind
  the numbers in `WORK_SO_FAR.md`.
- `progress.sh <run-dir>...` prints per-function outcome, turns, agent seconds
  and cost for a run in flight.
- `summarize_agent_runs.py <run-dir>...` aggregates a finished run, charging a
  session killed by the harness timeout its full timeout rather than zero.
- `compare_arms.py --baseline <label> --treatment <label> --runs <id>... --benchmarks <name>...`
  is the paired comparison that decides a treatment.
- `rescore_run.sh <label> <run-id> <benchmark>...` re-scores a run's
  specifications from one checkout. Use it whenever a scoring pass was
  interrupted, or to make sure both arms were scored by identical code.

## Two traps that cost real runs

- `AVOCADO_SCORER_ROOT` changes which *script* scores a run, but `uv` still
  resolves the *modules* from the arm's own checkout. Mixing them crashed two
  scoring passes. To score arms identically, run `rescore_run.sh` from a single
  checkout afterwards.
- Do not run `make clean-mutants` from the repository root while an experiment
  is running: it deletes in-flight artifacts under `avocado-experimental-data/`.
