#!/usr/bin/env bash
# Run an Avocado treatment on a subject program N times, each in a fresh container.
#
# Two kinds of treatment are supported, chosen with --runner (default: auto-detected from the
# harness checkout):
#   claude          the harness is instructions for a single `claude -p` session: the pure
#                   agentic baseline (AGENTS.md) or a CLAUDE.md-plus-tools treatment such as
#                   avocado-minus-harness. The prompt is sent from inside the subject copy.
#   avocado-verify  the harness ships its own driver, `avocado-verify --file <c-file>`, which
#                   runs one `claude -p` session per function and re-checks each result with
#                   CBMC (full-avocado and the other ablations). Auto-detected when the harness
#                   tracks avocado_verify.py. --file is required and --prompt is not accepted,
#                   because the driver writes its own prompts.
#
# Every run gets:
#   - its own container (`--rm`), so Claude Code's ~/.claude state, stray CBMC processes and
#     anything written under /app die with it;
#   - its own copy of the subject program, taken from the pristine `--subject` directory and
#     scrubbed of CBMC artifacts, mounted at /app/<subject-name>; the runner is launched from
#     inside it, as in the manual workflow, so a prompt may refer to ../AGENTS.md;
#   - a snapshot of the harness checkout, installed at /app inside the container, so AGENTS.md
#     or CLAUDE.md, .claude/settings.json and docs/ apply. A treatment that defines `avocado-*`
#     tools also gets its Python package, installed in the container with `uv sync` as `make run`
#     does. The snapshot holds only tracked files named in HARNESS_FILES (with their working-tree
#     contents), so stray logs, notes and these driver scripts never reach the agent. It is
#     saved as <batch>/harness/;
#   - optional per-container memory/CPU limits, so an OOM-killed CBMC only takes down that run.
#
# Results land on the host (default ~/.local/share/avocado-experiments, outside the repository
# and outside every container) under <results>/<tag>/<timestamp>/run-<i>/:
#   subject/            the subject copy, with the agent's specs and any files it wrote; with
#                       avocado-verify, also its <file>-avocado-verify.jsonl per-function log
#   claude-output.json  (claude runner) the `claude -p --output-format json` result
#   transcripts/        the Claude Code conversation transcript(s): <session-id>.jsonl per
#                       session, and <session-id>/subagents/ for any subagents it started
#   prompt.txt          (claude runner) the prompt that was sent
#   command.txt         the command line the runner was launched with
#   run.log             stdout+stderr of the run; with avocado-verify, the driver's log
#   exit-code           the container's exit code (124 = --timeout hit; 137 = killed, by the
#                       OOM killer or because it did not stop within a minute of --timeout;
#                       125 = the container could not be started, which fails the batch)
#
# Usage:
#   eval/experiments/run.sh --subject <dir> --profile <env-file> [--file <c-file>] \
#       [--runner auto|claude|avocado-verify] [--harness <checkout>] [--prompt <text>] \
#       [-n 5] [-j 1] [--timeout <sec>] \
#       [--memory 32g] [--cpus 8] [--results <dir>] [--tag <name>] [--image <image>]
#
# --harness names the treatment checkout to snapshot (default: the checkout this script is in),
# so one copy of these scripts can drive every treatment.
#
# With the claude runner the prompt is sent from inside the subject copy. Its default is
#   "Carefully read @../AGENTS.md. Then, verify the C source files in this directory"
# when the harness tracks an AGENTS.md ("Carefully read @../AGENTS.md. Then, verify {file}"
# with --file), and otherwise "Verify {file}" (which needs --file). It may use {file} for the
# path, inside the container, of the file named by --file (relative to --subject); a prompt
# that uses {file} needs --file.
#
# -n is the number of runs; -j (--jobs) is how many of them run concurrently, each in its own
# container with its own --memory/--cpus cap (default 1, i.e. sequential).
#
# See eval/experiments/profiles/ for the auth profiles (subscription vs. LiteLLM).
set -euo pipefail

# Detach from the terminal, so a dropped SSH connection cannot kill the batch. A hangup sends
# SIGHUP to everything launched from that terminal; `nohup` does not help, because `timeout`
# resets the handler and forwards the signal to `podman run`, which forwards it into the
# container (the run then ends with exit code 129). Re-executing in a session of our own, with
# no controlling terminal, keeps the signal out; stdin is detached too, and stdout and stderr
# are handled below. The parent waits and returns the batch's exit code, so a foreground launch
# still blocks until the batch is done; Ctrl-C only ends that wait. Stop a batch with
# `kill $(cat <batch>/run.pid)`, which stops its containers too.
if [ -z "${AVOCADO_DETACHED:-}" ] && command -v setsid >/dev/null 2>&1; then
  AVOCADO_DETACHED=1 exec setsid -w "${BASH_SOURCE[0]}" "$@" </dev/null
fi

SCRIPT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENGINE="${CONTAINER_ENGINE:-$(command -v docker 2>/dev/null || command -v podman 2>/dev/null || true)}"
IMAGE="${IMAGE_NAME:-avocado-agent-container}"
HARNESS_ROOT=""
RUNNER="auto"
RUNS=5
JOBS=1
MEMORY=""
CPUS=""
TIMEOUT=""
# Results live on the host, outside the repository and outside every container, so they survive
# container exit, `make clean`, and re-cloning the harness. Override with --results or
# AVOCADO_RESULTS_DIR.
RESULTS="${AVOCADO_RESULTS_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/avocado-experiments}"
# Seconds a stopped run (by --timeout or by stopping the batch) gets to end its session and
# publish its outputs before it is killed.
STOP_GRACE=60
TAG=""
SUBJECT=""
FILE=""
PROFILE=""
PROMPT=""
DEFAULT_PROMPT_AGENTS="Carefully read @../AGENTS.md. Then, verify the C source files in this directory"
DEFAULT_PROMPT_AGENTS_FILE="Carefully read @../AGENTS.md. Then, verify {file}"
DEFAULT_PROMPT_FILE="Verify {file}"

usage() { sed -n '2,/^set -euo/p' "${BASH_SOURCE[0]}" | grep '^#' | cut -c3-; exit "${1:-0}"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --subject) SUBJECT="$2"; shift 2 ;;
    --file) FILE="$2"; shift 2 ;;
    --profile) PROFILE="$2"; shift 2 ;;
    --prompt) PROMPT="$2"; shift 2 ;;
    --runner) RUNNER="$2"; shift 2 ;;
    --harness) HARNESS_ROOT="$2"; shift 2 ;;
    -n|--runs) RUNS="$2"; shift 2 ;;
    -j|--jobs) JOBS="$2"; shift 2 ;;
    --timeout) TIMEOUT="$2"; shift 2 ;;
    --memory) MEMORY="$2"; shift 2 ;;
    --cpus) CPUS="$2"; shift 2 ;;
    --results) RESULTS="$2"; shift 2 ;;
    --tag) TAG="$2"; shift 2 ;;
    --image) IMAGE="$2"; shift 2 ;;
    -h|--help) usage 0 ;;
    *) echo "unknown argument: $1" >&2; usage 1 ;;
  esac
done

[ -n "$ENGINE" ] || { echo "no docker or podman found" >&2; exit 1; }
[[ "$RUNS" =~ ^[1-9][0-9]*$ ]] || { echo "-n must be a positive integer: '$RUNS'" >&2; exit 1; }
[[ "$JOBS" =~ ^[1-9][0-9]*$ ]] || { echo "-j must be a positive integer: '$JOBS'" >&2; exit 1; }
[ -d "$SUBJECT" ] || { echo "--subject must be a directory: '$SUBJECT'" >&2; exit 1; }
[ -z "$FILE" ] || [ -f "$SUBJECT/$FILE" ] || { echo "--file must exist under the subject: '$SUBJECT/$FILE'" >&2; exit 1; }
[ -f "$PROFILE" ] || { echo "--profile must be an env file: '$PROFILE'" >&2; exit 1; }
HARNESS_ROOT="${HARNESS_ROOT:-$SCRIPT_ROOT}"
[ -d "$HARNESS_ROOT" ] || { echo "--harness must be a directory: '$HARNESS_ROOT'" >&2; exit 1; }
HARNESS_ROOT="$(cd "$HARNESS_ROOT" && pwd)"
if ! git -C "$HARNESS_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "$HARNESS_ROOT is not a git checkout; cannot tell harness files from stray ones" >&2
  exit 1
fi
# True iff the harness checkout tracks the given path (a file or a directory).
tracked() { git -C "$HARNESS_ROOT" ls-files --error-unmatch -- "$1" >/dev/null 2>&1; }

# The only harness files the agent may see, as git pathspecs: the instructions its prompt refers
# to (AGENTS.md, or CLAUDE.md and the docs/ it points at) and the Claude Code settings.
# Everything else in the checkout (driver scripts, logs, notes, build and CI files, tests) stays
# out. Extend this list deliberately if a treatment adds docs or tools.
HARNESS_FILES=(AGENTS.md CLAUDE.md docs .claude/settings.json)
# A treatment that defines command-line tools for the agent ([project.scripts] in
# pyproject.toml) also needs its Python package, which run-isolated.sh installs with
# `uv sync --frozen`: the project files (the build backend insists on README.md), the
# avocado-verify driver, the tools, the stubs/ that avocado-run-cbmc gives CBMC for library
# functions (found at /app/stubs), and the mutation code they import.
if tracked pyproject.toml && grep -q '^\[project\.scripts\]' "$HARNESS_ROOT/pyproject.toml"; then
  HARNESS_FILES+=(pyproject.toml uv.lock .python-version README.md avocado_verify.py tools
    stubs eval/__init__.py eval/mutants)
fi

SUBJECT="$(cd "$SUBJECT" && pwd)"
SUBJECT_NAME="$(basename "$SUBJECT")"
# The subject is mounted at /app/<name>, next to the harness snapshot and the .venv that
# run-isolated.sh creates, so its name must differ from each of their top-level names.
if [ "$SUBJECT_NAME" = / ] || [ "$SUBJECT_NAME" = .venv ] \
  || git -C "$HARNESS_ROOT" ls-files -z -- "${HARNESS_FILES[@]}" | tr '\0' '\n' | cut -d/ -f1 \
    | grep -xF -- "$SUBJECT_NAME" >/dev/null; then
  echo "--subject directory name '$SUBJECT_NAME' would shadow a harness path under /app; rename it" >&2
  exit 1
fi
PROFILE="$(cd "$(dirname "$PROFILE")" && pwd)/$(basename "$PROFILE")"

# Which driver runs the agent: the treatment's own `avocado-verify` when the harness tracks one,
# otherwise a single `claude -p` session.
case "$RUNNER" in
  auto) if tracked avocado_verify.py; then RUNNER=avocado-verify; else RUNNER=claude; fi ;;
  claude|avocado-verify) ;;
  *) echo "--runner must be auto, claude or avocado-verify: '$RUNNER'" >&2; exit 1 ;;
esac
if [ "$RUNNER" = avocado-verify ]; then
  [ -n "$FILE" ] || { echo "--file is required with the avocado-verify runner" >&2; exit 1; }
  [ -z "$PROMPT" ] || { echo "--prompt is not used by the avocado-verify runner; it writes its own prompts" >&2; exit 1; }
  tracked avocado_verify.py || { echo "--runner avocado-verify needs a tracked avocado_verify.py in $HARNESS_ROOT" >&2; exit 1; }
elif [ -z "$PROMPT" ]; then
  if tracked AGENTS.md && [ -n "$FILE" ]; then
    PROMPT="$DEFAULT_PROMPT_AGENTS_FILE"
  elif tracked AGENTS.md; then
    PROMPT="$DEFAULT_PROMPT_AGENTS"
  elif [ -n "$FILE" ]; then
    PROMPT="$DEFAULT_PROMPT_FILE"
  else
    echo "$HARNESS_ROOT has no AGENTS.md for the default prompt; give --file (prompt \"$DEFAULT_PROMPT_FILE\") or --prompt" >&2
    exit 1
  fi
fi
if [[ "$PROMPT" == *"{file}"* ]] && [ -z "$FILE" ]; then
  echo "the prompt uses {file}, so --file is required: '$PROMPT'" >&2
  exit 1
fi

# The default tag includes the harness directory name, so several checkouts (e.g. one per
# treatment) launched against the same subject keep their results apart.
TAG="${TAG:-$(basename "$HARNESS_ROOT")-$(basename "$SUBJECT")-$(basename "${PROFILE%.env}")}"
TAG="$(printf '%s' "$TAG" | tr -c 'A-Za-z0-9_.-' '-')"
# A random suffix makes the batch id unique even when two batches start in the same second.
BATCH_ID="$(date -u +%Y%m%dT%H%M%SZ)-$(head -c 3 /dev/urandom | od -An -tx1 | tr -d ' \n')"
BATCH_DIR="$RESULTS/$TAG/$BATCH_ID"
mkdir -p "$BATCH_DIR"
# A detached batch still writes to the terminal it was launched from. Once that terminal is gone
# (or a `| tee` reading it has died), a write fails, and under `set -e` a failed `echo` would end
# the batch. Send all output through a `tee` that keeps going when the terminal fails (-p) and
# also writes `batch.log`.
if [ -n "${AVOCADO_DETACHED:-}" ]; then
  exec > >(tee -p -a "$BATCH_DIR/batch.log") 2>&1
fi
echo "results: $BATCH_DIR"

# `kill <pid>` (TERM or INT) stops this batch's containers; each run then publishes what it has
# and records its exit code as usual before this script exits. The PID file is removed on exit,
# so a stale one cannot direct that `kill` at an unrelated process that reuses the PID.
echo $$ >"$BATCH_DIR/run.pid"
trap 'rm -f "$BATCH_DIR/run.pid"' EXIT
stop_batch() {
  trap - TERM INT
  echo "stopping batch $BATCH_ID" >&2
  # A run that is still copying its subject starts its container after any one scan, so keep
  # stopping containers until every run has ended.
  while [ -n "$(jobs -rp)" ]; do
    "$ENGINE" ps -q --filter "name=avocado-$TAG-$BATCH_ID-" \
      | xargs -r "$ENGINE" stop -t "$STOP_GRACE" >/dev/null 2>&1 || true
    sleep 1
  done
  exit 143
}
trap stop_batch TERM INT

# What the runner is given: the prompt, or (avocado-verify) the C file's path inside the
# container.
IN_FILE=""
[ -z "$FILE" ] || IN_FILE="/app/$SUBJECT_NAME/$FILE"
PROMPT="${PROMPT//\{file\}/$IN_FILE}"
if [ "$RUNNER" = avocado-verify ]; then
  TARGET="$IN_FILE"
else
  TARGET="$PROMPT"
fi


# Snapshot the harness the agent will see, with working-tree contents (so uncommitted edits to
# AGENTS.md or .claude/settings.json are included). Only tracked files matching HARNESS_FILES
# are taken, so an untracked file can never leak in even if it matches.
snapshot_harness() {
  local dest="$1"
  mkdir -p "$dest"
  git -C "$HARNESS_ROOT" ls-files -z --cached -- "${HARNESS_FILES[@]}" \
    | (cd "$HARNESS_ROOT" && tar --null --ignore-failed-read -T - -cf -) \
    | tar -C "$dest" -xf -
  (cd "$dest" && find . -type f | sort) >"$BATCH_DIR/harness-files.txt"
  if ! grep -qx -e './AGENTS.md' -e './CLAUDE.md' "$BATCH_DIR/harness-files.txt"; then
    echo "neither AGENTS.md nor CLAUDE.md is a tracked file in $HARNESS_ROOT; the agent depends on one" >&2
    exit 1
  fi
}
snapshot_harness "$BATCH_DIR/harness"

# What the container receives on stdin: the driver script plus the harness snapshot, as one tar
# stream. Nothing of the host checkout is mounted, so the agent cannot discover it through
# /proc/mounts. The only mounts it can see are its subject copy, an empty output dir and, if
# the login volume exists, /auth. The agent can read its credentials (/auth, its config dir,
# and the profile's token variables), so they can end up in the results.
boot_stream() {
  tar -C "$SCRIPT_ROOT/docker" -cf - run-isolated.sh
  tar -C "$BATCH_DIR" -cf - harness
}

# A `claude` login persisted by `eval/experiments/login.sh volume`, mounted read-only. It is used
# only with a profile that supplies no credentials of its own, so a run never has two.
AUTH_VOLUME="avocado-claude-auth"
AUTH_MOUNT=()
if ! grep -qE '^[[:space:]]*(CLAUDE_CODE_OAUTH_TOKEN|ANTHROPIC_AUTH_TOKEN|ANTHROPIC_API_KEY)=' "$PROFILE"; then
  if "$ENGINE" volume inspect "$AUTH_VOLUME" >/dev/null 2>&1; then
    AUTH_MOUNT=(-v "$AUTH_VOLUME:/auth:ro")
  else
    echo "warning: $PROFILE sets no token and there is no $AUTH_VOLUME volume; runs have no credentials" >&2
  fi
fi

LIMITS=()
[ -n "$MEMORY" ] && LIMITS+=(--memory "$MEMORY" --memory-swap "$MEMORY")
[ -n "$CPUS" ] && LIMITS+=(--cpus "$CPUS")

# Wall-clock cap on a whole run. `timeout` sends SIGTERM to the `$ENGINE run` client, which
# forwards it into the container, so the run publishes its outputs and exits (124). If the
# container has not stopped STOP_GRACE seconds later, `timeout` kills the client (137) and
# run_one stops the container with `$ENGINE stop`.
with_timeout() {
  if [ -n "$TIMEOUT" ]; then
    timeout --foreground --kill-after="$STOP_GRACE" "$TIMEOUT" "$@"
  else
    "$@"
  fi
}

# Make a pristine copy of the subject and remove anything a previous Avocado run may have left.
copy_subject() {
  local dest="$1"
  mkdir -p "$dest"
  tar -C "$SUBJECT" --exclude=.git -cf - . | tar -C "$dest" -xf -
  find "$dest" \( -name '*.goto' -o -name '*-callgraph.json' -o -name '*.jsonl' \
    -o -name '*__mutant_*.c' -o -name '*__clause_drop_*.c' \
    -o -name '*mutation_compile_failures.log' -o -name '__pycache__' \) -prune -exec rm -rf {} +
}

run_one() {
  local i="$1"
  local run_dir="$BATCH_DIR/run-$i"
  # Container names must be unique host-wide, across every clone and concurrent batch.
  local name="avocado-$TAG-$BATCH_ID-run$i"
  copy_subject "$run_dir/subject"
  mkdir -p "$run_dir/out"
  echo "[$TAG] run $i/$RUNS -> $run_dir"
  set +e
  # `tar` here is concatenated archives; GNU tar needs -i to read past the first end-of-archive.
  boot_stream | with_timeout "$ENGINE" run --rm -i --name "$name" \
    --env-file "$PROFILE" \
    -e PYTHONDONTWRITEBYTECODE=1 \
    -e DISABLE_AUTOUPDATER=1 \
    -e IS_SANDBOX=1 \
    -e SUBJECT_NAME="$SUBJECT_NAME" \
    "${LIMITS[@]}" \
    "${AUTH_MOUNT[@]}" \
    -v "$run_dir/subject:/app/$SUBJECT_NAME" \
    -v "$run_dir/out:/work/out" \
    --entrypoint /bin/bash \
    "$IMAGE" -c 'set -e; b=$(mktemp -d); tar -C "$b" -ixf -; exec "$b/run-isolated.sh" "$b" "$@"' \
    run-isolated "$RUNNER" "$TARGET" \
    >"$run_dir/run.log" 2>&1
  local rc=$?
  set -e
  if [ "$rc" -eq 124 ] || [ "$rc" -eq 137 ]; then
    "$ENGINE" stop -t 5 "$name" >/dev/null 2>&1 || true
  fi
  # A rootful engine leaves what the container wrote (the outputs, and the agent's files under
  # subject/) owned by the host's root, which the user can neither move nor delete; hand it
  # back. With rootless docker or podman, the container's root already maps to the user.
  if [ "$(id -u)" -ne 0 ] && [ -n "$(find "$run_dir" -user 0 -print -quit 2>/dev/null)" ]; then
    "$ENGINE" run --rm -v "$run_dir:/run-dir" --entrypoint chown "$IMAGE" \
      -R "$(id -u):$(id -g)" /run-dir >/dev/null 2>&1 \
      || echo "[$TAG] run $i: could not take ownership of the files root owns under $run_dir" >&2
  fi
  # Flatten: keep the container-facing /work/out layout out of the way of readers.
  if compgen -G "$run_dir/out/*" >/dev/null; then
    mv "$run_dir"/out/* "$run_dir"/ \
      || echo "[$TAG] run $i: could not move its results out of $run_dir/out" >&2
  fi
  rmdir "$run_dir/out" 2>/dev/null || true
  echo "$rc" >"$run_dir/exit-code"
  echo "[$TAG] run $i/$RUNS finished with exit code $rc"
}

{
  echo "subject=$SUBJECT"
  echo "file=${FILE:-<none>}"
  echo "profile=$PROFILE"
  echo "image=$IMAGE"
  echo "runner=$RUNNER"
  echo "prompt=${PROMPT:-<none>}"
  echo "runs=$RUNS jobs=$JOBS timeout=${TIMEOUT:-none} memory=${MEMORY:-unlimited} cpus=${CPUS:-unlimited}"
  echo "harness_dir=$HARNESS_ROOT"
  echo "harness_branch=$(git -C "$HARNESS_ROOT" branch --show-current 2>/dev/null || echo unknown)"
  echo "harness_commit=$(git -C "$HARNESS_ROOT" rev-parse HEAD 2>/dev/null || echo unknown)"
  echo "scripts_dir=$SCRIPT_ROOT"
} >"$BATCH_DIR/batch-info.txt"

for i in $(seq 1 "$RUNS"); do
  run_one "$i" &
  if [ "$(jobs -rp | wc -l)" -ge "$JOBS" ]; then
    # A run that failed outside its container (e.g. copying the subject) must not end the
    # batch; it is reported below.
    wait -n || true
  fi
done
# Not a bare `wait`, which would also wait for the `tee` above, and that never ends.
while [ -n "$(jobs -rp)" ]; do
  wait -n || true
done
# The batch fails if any run did not get as far as its container. A runner's own failure is a
# result of the experiment, not of the batch. Docker and podman exit with 125 when they cannot
# start the container.
failed=0
for i in $(seq 1 "$RUNS"); do
  if [ ! -f "$BATCH_DIR/run-$i/exit-code" ]; then
    echo "[$TAG] run $i/$RUNS failed before its container finished; see the error above" >&2
    failed=1
  elif [ "$(cat "$BATCH_DIR/run-$i/exit-code")" = 125 ]; then
    echo "[$TAG] run $i/$RUNS: $ENGINE could not start its container; see $BATCH_DIR/run-$i/run.log" >&2
    failed=1
  fi
done
echo "done: $BATCH_DIR"
exit "$failed"
