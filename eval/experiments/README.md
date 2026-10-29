# Isolated and Repeatable Experiment Runs

`eval/experiments/run.sh` runs one treatment on a subject program *n* times, each in a fresh
container, so no run can see another's specs, Claude Code session transcripts or memory. It
drives both kinds of treatment:

- **`claude` runner**: a single `claude -p` session over the subject, as for the pure agentic
  baseline (`AGENTS.md`) or a treatment that does not have tools which call `claude`
  (e.g., `avocado-minus-harness`).
- **`avocado-verify` runner**: the treatment's own driver, `avocado-verify --file <c-file>`,
  which runs one `claude -p` session per function and re-checks each result with CBMC
  (`full-avocado` and the other non-tool ablations). It is picked automatically when the harness
  checkout tracks `avocado_verify.py`; `--runner` overrides the choice.

Which checkout is the harness is `--harness <dir>` (default: the checkout holding `run.sh`),
so one copy of these scripts can drive every treatment:

```sh
% eval/experiments/run.sh --harness ../opus-5-5/full-avocado --subject path/to/subject \
      --file src/foo.c --profile eval/experiments/profiles/subscription.env -n 5 --memory 100g
```

The agent sees nothing of the harness checkout but the files `run.sh` names in
`HARNESS_FILES`, taken from tracked files only: `AGENTS.md` or `CLAUDE.md`, `docs/` and
`.claude/settings.json`, plus, for a treatment whose `pyproject.toml` defines `avocado-*`
tools, the Python package those tools come from (`pyproject.toml`, `uv.lock`,
`.python-version`, `README.md`, `avocado_verify.py`, `tools/`, `eval/__init__.py`,
`eval/mutants/`). Driver scripts, logs, notes, tests and build or CI files never reach it. To
give a treatment extra docs or tools, track them and add them to `HARNESS_FILES` deliberately.

- Each run starts a new `--rm` container; `~/.claude` (via a throwaway `CLAUDE_CONFIG_DIR`),
  `/app`, and any stray `cbmc` processes die with it.
- Each run gets its own copy of the subject, taken from the pristine `--subject` directory and
  scrubbed of `*.goto`, `*-callgraph.json`, `*.jsonl`, mutant files, etc. It is mounted at
  `/app/<subject-name>` and the runner is launched from inside it, mirroring the manual
  workflow in `EXPERIMENTAL_PROCEDURE.md`, so a prompt can refer to `../AGENTS.md` and "this
  directory", and an `/app/CLAUDE.md` is picked up as a parent-directory instructions file.
- Nothing of the checkout is mounted. The driver script and the harness snapshot are streamed
  into the container on stdin; the snapshot is installed at `/app` and the bootstrap directory
  deleted before the runner starts. If the snapshot carries a Python package it is installed
  into `/app/.venv` with `uv sync --frozen`, exactly as `docker/entrypoint.sh` does for
  `make run`, which puts `avocado-run-cbmc` and friends (and `avocado-verify`) on `PATH`. This
  downloads Python 3.13 and the dependencies afresh in every container, which costs a minute
  or so per run. The only mounts the agent can find are its own subject copy and an empty
  output directory, which is filled (result JSON, transcripts, prompt) only after the session
  ends. The snapshot is kept as `<batch>/harness/` with a `harness-files.txt` listing, so every
  batch records exactly what its agent was given.
- Results are written to the **host**, outside the repository and outside every container, so
  they persist after each container exits: by default
  `~/.local/share/avocado-experiments/<tag>/<timestamp>/run-<i>/` holds `subject/`,
  `transcripts/`, `command.txt`, `run.log`, `exit-code` and, with the `claude` runner,
  `claude-output.json` and `prompt.txt`. The batch directory holds `batch-info.txt`
  recording the runner, the harness commit and the parameters. Override the location with
  `--results <dir>` or `AVOCADO_RESULTS_DIR`.
- `--memory`/`--cpus` cap each container, so an OOM-killed CBMC only costs that one run,
  `--timeout <sec>` caps a run's wall-clock time, and `-j` runs several containers at once.

A worked example, full Avocado on `lz4_lib` with Opus 5.5, is in [EXAMPLE.md](EXAMPLE.md).

## Step 1: build the image

```sh
% make build-image
```

Every treatment's `Dockerfile` is the same, so one image serves them all.

## Step 2: log in once

Pick one auth profile under `eval/experiments/profiles/` (the `*.env` files are gitignored):

- **Claude subscription**: run `eval/experiments/login.sh`; it prints a long-lived OAuth token.
  Copy `subscription.env.example` to `subscription.env` and paste the token in.
  (Alternatively `login.sh volume` stores an interactive `claude auth login` on a named volume
  that every run seeds its fresh config directory from.)
- **LiteLLM-hosted model**: copy `litellm.env.example` to `litellm.env` and set the proxy URL,
  key, and the model name (forwarded to `claude --model`, overriding `.claude/settings.json`).

Which model a run uses: `AVOCADO_MODEL` from the profile if set, otherwise the `"model"` key in
`.claude/settings.json`, passed explicitly as `claude --model`. (Claude Code ignores the
`model` key of a file given with `--settings`, so without this the account default, currently
Sonnet, would be used.) `avocado-verify` builds its own `claude` command line, so with that
runner `--settings` and `--model` reach every session through a `claude` wrapper placed first
on `PATH`. A treatment without `.claude/settings.json` (e.g. `avocado-minus-mutation-testing`)
and without `AVOCADO_MODEL` runs on the account default. `run.log` prints the runner and the
model on its first line and, with the `claude` runner, `claude-output.json`'s `modelUsage`
records what was actually billed (with `avocado-verify`, the per-function records in
`subject/<file>-avocado-verify.jsonl` carry each session's cost); check these after launching
a new treatment.

## Step 3: launch a batch

```sh
% eval/experiments/run.sh --subject path/to/subject \
      --profile eval/experiments/profiles/subscription.env -n 5 --memory 100g
```

With the `claude` runner the default prompt is
`Carefully read @../AGENTS.md. Then, verify the C source files in this directory` when the
harness tracks an `AGENTS.md`, and otherwise `Verify {file}` (as in the manual procedure),
which needs `--file`. The prompt is sent from inside the subject copy. `--prompt` replaces it;
`--file <c-file>` (relative to `--subject`) fills a `{file}` placeholder in the prompt with that
file's in-container path.

With the `avocado-verify` runner `--file` is required and `--prompt` is rejected: the driver
writes its own per-function prompts, and the run is `avocado-verify --file /app/<subject>/<c-file>`
from inside the subject copy.

`-n` is the number of runs and `-j N` (`--jobs`) how many of them run concurrently, each in its
own container with its own `--memory`/`--cpus` cap (default 1, i.e. sequential; budget memory
as `-j` × `--memory`). `--tag` names the batch (default: harness directory name, subject name
and profile name). 

## Step 4: read the results

Each `run-<i>/exit-code` is the runner's exit code (`124` means `--timeout` was hit;
`avocado-verify` exits `2` when it stopped on a usage limit). `transcripts/` has the full
conversation(s) and `subject/` the specified program. With the `claude` runner
`claude-output.json` has the session id, cost and final message. With `avocado-verify`,
`run.log` is the driver's log (what the manual procedure saved as `claude-output.json`) and
`subject/<file>-avocado-verify.jsonl` holds one record per function plus a final
`run_summary` record.
