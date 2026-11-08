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
`.python-version`, `README.md`, `avocado_verify.py`, `tools/`, the `stubs/` that
`avocado-run-cbmc` gives CBMC, `eval/__init__.py`, `eval/mutants/`). Driver scripts, logs, notes, tests and build or CI files never reach it. To
give a treatment extra docs or tools, track them and add them to `HARNESS_FILES` deliberately.

- Each run starts a new `--rm` container; `~/.claude` (via a throwaway `CLAUDE_CONFIG_DIR`),
  `/app`, and any stray `cbmc` processes die with it.
- Each run gets its own copy of the subject, taken from the pristine `--subject` directory and
  scrubbed of `*.goto`, `*-callgraph.json`, `*.jsonl`, mutant files, etc. It is mounted at
  `/app/<subject-name>` and the runner is launched from inside it, mirroring the manual
  workflow, so a prompt can refer to `../AGENTS.md` and "this
  directory", and an `/app/CLAUDE.md` is picked up as a parent-directory instructions file.
- Nothing of the checkout is mounted. The driver script and the harness snapshot are streamed
  into the container on stdin; the snapshot is installed at `/app` and the bootstrap directory
  deleted before the runner starts. If the snapshot carries a Python package it is installed
  into `/app/.venv` with `uv sync --frozen --no-dev`, as `docker/entrypoint.sh` does for
  `make run`, which puts `avocado-run-cbmc` and friends (and `avocado-verify`) on `PATH`;
  commands whose modules the snapshot leaves out (such as `save-session-metadata`) are
  removed. The image already holds Python and the locked dependencies (Step 1), so this takes
  seconds. The only mounts the agent can find are its own subject copy, an empty output
  directory, which is filled (result JSON, transcripts, prompt) only after the session ends,
  and, with a profile that sets no token, the login volume at `/auth` (read-only). The snapshot
  is kept as `<batch>/harness/` with a `harness-files.txt` listing, so every batch records
  exactly what its agent was given.
- Results are written to the **host**, outside the repository and outside every container, so
  they persist after each container exits: by default
  `~/.local/share/avocado-experiments/<tag>/<timestamp>/run-<i>/` holds `subject/`,
  `transcripts/`, `command.txt`, `run.log`, `exit-code`, `outside-subject/` (if the agent
  wrote files outside its subject copy) and, with the `claude` runner,
  `claude-output.json` and `prompt.txt`. The batch directory holds `batch-info.txt`
  recording the runner, the harness commit and the parameters, and `batch.log` with the
  batch's progress lines. Override the location with `--results <dir>` or
  `AVOCADO_RESULTS_DIR`. With rootful docker, whose containers write files as the host's root,
  each run's files are handed back to the user when the run ends.
- `--memory`/`--cpus` cap each container, so an OOM-killed CBMC only costs that one run,
  `--timeout <sec>` caps a run's wall-clock time, and `-j` runs several containers at once.
- The agent is not isolated from its own credentials. It runs with
  `--dangerously-skip-permissions` and can read the profile's token variables
  (`CLAUDE_CODE_OAUTH_TOKEN`, `ANTHROPIC_AUTH_TOKEN`), the login copied into its config
  directory, and `/auth`; if it prints one, the token lands in the transcripts or `subject/`.
  Use a token you can revoke, and review results before sharing them.

## Step 1: build the image

```sh
% make build-image
```

Every treatment's `Dockerfile` is the same, so one image serves them all. The image includes
Python and uv's cache of the dependencies in `uv.lock`, so rebuild it after `uv.lock` changes;
an out-of-date image still works, but every run downloads what the cache lacks.

## Step 2: log in once

Pick one auth profile under `eval/experiments/profiles/` (the `*.env` files are gitignored):

- **Claude subscription**: run `eval/experiments/login.sh`; it prints a long-lived OAuth token.
  Copy `subscription.env.example` to `subscription.env` and paste the token in.
  (Alternatively `login.sh volume` stores an interactive `claude auth login` on a named volume
  that every run seeds its fresh config directory from. The volume is used only with a profile
  that sets none of `CLAUDE_CODE_OAUTH_TOKEN`, `ANTHROPIC_AUTH_TOKEN` and
  `ANTHROPIC_API_KEY`, such as an empty file, so a run never has two sets of credentials.)
- **LiteLLM-hosted model**: copy `litellm.env.example` to `litellm.env` and set the proxy URL,
  key, and the model name (forwarded to `claude --model`, overriding `.claude/settings.json`).
  
### What Model Does A Run Use?

There are two ways to set a model for an experimental run:

- `AVOCADO_MODEL` from the profile if set
- The `"model"` key in `.claude/settings.json` in the harness checkout of `avocado-agent`.

If neither of these are set (e.g., `avocado-minus-mutation-testing`), then the experiment will
run on the account default.

`run.log` prints the runner and the model on its first line and, with the `claude` runner,
`claude-output.json`'s `modelUsage` records what was actually billed
(with `avocado-verify`, the per-function records in `subject/<stem>-avocado-verify.jsonl` carry
each session's cost, where `<stem>` is the `--file` path without its extension, so `--file
src/foo.c` gives `subject/src/foo-avocado-verify.jsonl`); check these after launching a new
treatment.

## Step 3: launch a batch

```sh
% eval/experiments/run.sh --harness path/to/harness --subject path/to/subject \
      --profile eval/experiments/profiles/subscription.env -n 5 --memory 100g
```

With the `claude` runner the default prompt is
`Carefully read @../AGENTS.md. Then, verify the C source files in this directory` when the
harness tracks an `AGENTS.md` (`Carefully read @../AGENTS.md. Then, verify {file}` with
`--file`), and otherwise `Verify {file}` (as in the manual procedure), which needs `--file`.
The prompt is sent from inside the subject copy. `--prompt` replaces it; `--file <c-file>`
(relative to `--subject`) fills a `{file}` placeholder in the prompt with that file's
in-container path, and a prompt with a `{file}` placeholder requires `--file`.

With the `avocado-verify` runner `--file` is required and `--prompt` is rejected: the driver
writes its own per-function prompts, and the run is `avocado-verify --file /app/<subject>/<c-file>`
from inside the subject copy.

- `-n`: is the number of runs.
- `-j N`: (`--jobs`) is the number of concurrent runs that each run in their own container with its
  own `--memory`/`--cpus` cap.
- `--tag` (optional): names the batch (default: harness directory name, subject name
  and profile name).

A batch detaches from the terminal it was launched from (it re-executes itself under
`setsid`), so closing that terminal or losing an SSH connection does not kill it; `nohup` is
not needed. The batch's progress lines go both to that terminal (while it exists) and to
`batch.log` in the batch directory. Ctrl-C
therefore does not stop a batch either. To stop one, kill the process recorded in its
`run.pid` (which exists only while the batch runs):

```sh
% kill $(cat ~/.local/share/avocado-experiments/<tag>/<timestamp>/run.pid)
```

This stops the batch's containers, giving each a minute to end its session; each run then
saves its transcript and records exit code `143`.

### Resuming Stopped Runs

An `avocado-verify` run that stopped early can be continued. A run stops early, and records a
non-zero `exit-code`, in one of these ways:

- `2`: a session hit a subscription usage limit. The driver stops by itself: it writes a
  `run_summary` record with `"status": "usage_limited"` and the functions still to process,
  and exits.
- `124`: the run hit `--timeout`.
- `137` or `143`: the container was killed, `143` when its batch was stopped with `kill`.

`--resume` continues **one** run, given its run directory:

```sh
% eval/experiments/run.sh --resume ~/.local/share/avocado-experiments/<tag>/<batch>/run-1
```

This starts one new run, in a new batch under the same tag, from the stopped run's `subject/`
(its specs so far and the driver's `*.jsonl` logs) rather than a pristine copy, and passes the
driver `--resume-from <stem>-avocado-verify.jsonl` (`<stem>` is the `--file` path without its
extension, as above). The driver skips the functions already
processed, retries the one that was cut off, and appends to the same log, so the resumed run's
`subject/<stem>-avocado-verify.jsonl` covers the whole run. The subject, file, harness, runner
and profile default to those in the stopped batch's `batch-info.txt`, which for the new batch
also records `resumed_from`. `--profile` may name another account, as long as it sets the same
`AVOCADO_MODEL`; `--timeout`, `--memory` and `--cpus` are not carried over, so pass them again.
The `claude` runner (one session) cannot be resumed.

#### Resuming a stopped batch

There is no batch-level resume. To continue a batch, resume each of its stopped runs on its own;
each becomes a new single-run batch under the same tag. Runs with exit code `0` are complete and
need nothing. Each `--resume` blocks until its run ends, so put it in the background (`&`) to
resume several runs at once:

```sh
% BATCH=~/.local/share/avocado-experiments/<tag>/<batch>
% for run in "$BATCH"/run-*/; do
    case "$(cat "$run/exit-code")" in
      2|124|137|143) eval/experiments/run.sh --resume "$run" --memory 100g & ;;
    esac
  done
```

A batch that hits a usage limit still starts every run, and each of those runs stops on the
limit with exit code `2`. All of them can therefore be resumed this way.

## Step 4: read the results

Each `run-<i>/exit-code` is the runner's exit code. `124` means `--timeout` was hit; `137`
means the container was killed, by the out-of-memory killer or because it did not stop within a
minute of hitting `--timeout`; `125` means the container engine could not start the container,
and then `run.sh` itself exits with status 1. `avocado-verify` exits `2` when it stopped on a
usage limit. `transcripts/` has the full conversation(s), as `<session-id>.jsonl` plus
`<session-id>/subagents/*.jsonl` for any subagents, and `subject/` the specified program.
`outside-subject/` keeps every file the agent created or changed outside `subject/` during the
run (stubs, harnesses and scripts under `/tmp`, `/work`, `/app/scripts`, ...), at its absolute path in
the container, e.g. `outside-subject/tmp/work/strstr_stub.c`. It leaves out the harness's own
scratch directories, CBMC `*.goto` files and dot-directories under `$HOME`; files over 10 MiB
are listed in `outside-subject/SKIPPED.txt` instead of copied.
With the `claude` runner `claude-output.json` has the session id, cost and final message.
With `avocado-verify`, `run.log` is the driver's log (what the manual procedure saved as
`claude-output.json`) and `subject/<stem>-avocado-verify.jsonl` holds one record per function
plus a final `run_summary` record.

## Step 5: save a run

`eval/experiments/save_run.py` copies a finished run into the
[`avocado-experimental-data`](https://github.com/jyoo980/avocado-experimental-data) repository
(default: `$AVOCADO_DATA_DIR`, else a checkout next to this one; override with `--data-dir`):

```sh
% eval/experiments/save_run.py --model opus-5-5 --treatment avocado-minus-tools \
      ~/.local/share/avocado-experiments/<tag>/<batch>/run-1
```

For a run continued with `--resume`, pass the original run directory and then each resumed run
directory, in order; the script checks that each one's `resumed_from` names the one before it.
The run is saved as:

```
<data-dir>/<treatment>/<model>/<subject>/
    ...                  the last run's subject/, minus *.goto and the top-level *.log files
    claude-files/        every run's transcripts/, plus run.log, run-pt2.log, ... and
                         (claude runner) claude-output.json
    outside-subject/     every run's outside-subject/ (files the agent wrote outside the
                         subject, such as stubs under /tmp), merged
```

The script warns if the run did not exit `0` or if the batch's harness is not
`.../<model>/<treatment>`, refuses to overwrite an existing destination without `--force`, and
`--dry-run` shows what would be copied. It does not commit: review the files and commit them in the
data repository yourself.

## Worked Example

Assume you want to run the baseline Claude Code system to generate specs for the `kilo` subject
    program.

1. Set up the harness; clone `avocado-agent` and switch to the
   `yoo/treatments/pure-agentic-baseline` branch:
   ```sh
   % git clone -b yoo/treatments/pure-agentic-baseline git@github.com:jyoo980/avocado-agent.git pure-agentic-baseline
   ```
   This clones the `avocado-agent` repository's agentic baseline branch to the working directory.
2. Set up the subject program:
   ```sh
   % mkdir subject-programs
   % cd subject-programs && git clone git@github.com:antirez/kilo.git
   ```
   This creates `subject-programs/kilo`.
3. Obtain a copy of the experimental scripts (clone the `main` branch of the avocado-agent repo) and
   build the image:
   ```sh
   % git clone git@github.com:jyoo980/avocado-agent.git
   % cd avocado-agent && make build-image
   ```
4. Obtain the long-lived auth token you want to run experiments under (see earlier section titled "Step 2: log in once")
   and save the key to the profile you create (e.g., under `avocado-profiles/anthropic.env`).
5. Start the run. The command below will launch 3 runs of the pure agentic approach across
   3 containers,
   each with 100g of memory.
   From the top-level directory holding both `avocado-agent`,
       the harness,
       the subject program directory,
       and the auth profiles,
       run:

   ```sh
   % ~/avocado-agent/eval/experiments/run.sh --harness ~/pure-agentic-baseline \
       --subject ~/subject-programs/kilo \
       --profile ~/avocado-profiles/anthropic.env \
       -n 3 -j 3 \
       --timeout 129600 \
       --memory 100g > ~/avocado-agent/pure-agentic-baseline-kilo.log 2>&1 &
   ```
6. Check the run(s) have launched by running `docker container ls`.
