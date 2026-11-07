#!/usr/bin/env bash

# Run one Avocado session over a subject program in a throwaway environment inside the
# container, exposing nothing to the agent but the subject and the harness snapshot.
#
# Invoked by `eval/experiments/run.sh` through a bootstrap that extracts a tar stream from
# stdin into a temporary directory and execs this script from it. It expects:
#   $1                   that bootstrap directory, holding this script and `harness/` (the
#                        snapshot of the harness files made by run.sh)
#   $2                   the runner: `claude` (one `claude -p` session) or `avocado-verify`
#                        (the treatment's own driver, which runs one `claude -p` session per
#                        function and re-checks each result with CBMC)
#   $3                   with `claude`, the prompt to send; with `avocado-verify`, the path
#                        inside the container of the C file to verify
#   $SUBJECT_NAME        the subject directory's name (env)
#   $AVOCADO_RESUME      (optional, env) non-empty when `run.sh --resume` continues a stopped
#                        avocado-verify run; the driver then gets `--resume-from` its own log
#   /app/$SUBJECT_NAME   read-write bind mount of a fresh copy of the subject program
#   /work/out            read-write bind mount, empty until this script exits
#   /auth                (optional) read-only volume holding a `claude` login
#
# The layout mirrors the manual workflow: the harness is installed
# at /app, the subject lives inside it, and the runner is launched from inside the subject
# directory.
#
# A prompt may refer to `../AGENTS.md` and "this directory", and an `/app/CLAUDE.md` is
# picked up by Claude Code as a parent-directory instructions file. If the snapshot carries a
# Python package (pyproject.toml + uv.lock), it is installed into /app/.venv exactly as
# docker/entrypoint.sh does for `make run`, which puts the treatment's `avocado-*` tools (and
# `avocado-verify` itself) on PATH. `/app` is the container-private volume, so nothing written
# there persists.

# Claude Code gets an empty config directory (no session transcripts or memory
# survive between runs), seeded with credentials from `/auth` if present.

# All outputs are collected in a private directory and copied to /work/out only when the session has
# ended. They include `outside-subject/`: every file the agent created or changed outside the
# subject copy (e.g. a stub it wrote under /tmp), so nothing it relied on is lost with the
# container. The agent never sees a log, a result file, or this script: the bootstrap directory is
# deleted once the harness has been installed. If `AVOCADO_MODEL` is set
# (e.g. by a LiteLLM profile), it is forwarded as `--model`. `avocado-verify` builds its own
# `claude` command, so with that runner `--settings` and `--model` reach every session through a
# `claude` wrapper placed first on PATH.
set -euo pipefail

if [ $# -ne 3 ] || [ -z "${SUBJECT_NAME:-}" ]; then
  echo "usage: SUBJECT_NAME=<name> run-isolated.sh <bootstrap-dir> <claude|avocado-verify> <prompt|c-file>" >&2
  exit 2
fi
BOOT_DIR="$1"
RUNNER="$2"
TARGET="$3"
SUBJECT_DIR="/app/$SUBJECT_NAME"
# Set by `run.sh --resume`: the subject copy is a stopped run's, and the driver continues its log.
# Unset so the agent's shells do not inherit it.
RESUME="${AVOCADO_RESUME:-}"
unset AVOCADO_RESUME

case "$RUNNER" in
  claude|avocado-verify) ;;
  *) echo "run-isolated: unknown runner '$RUNNER' (expected claude or avocado-verify)" >&2; exit 2 ;;
esac
if [ ! -f "$BOOT_DIR/harness/AGENTS.md" ] && [ ! -f "$BOOT_DIR/harness/CLAUDE.md" ]; then
  echo "run-isolated: expected the harness snapshot (AGENTS.md or CLAUDE.md) at $BOOT_DIR/harness" >&2
  exit 2
fi
if [ ! -d "$SUBJECT_DIR" ]; then
  echo "run-isolated: expected the subject program at $SUBJECT_DIR" >&2
  exit 2
fi
if [ "$RUNNER" = avocado-verify ] && [ ! -f "$TARGET" ]; then
  echo "run-isolated: expected the C file to verify at $TARGET" >&2
  exit 2
fi

# Outputs accumulate here and are published to /work/out on exit, whether the session
# finishes, fails, or is stopped by the host (SIGTERM reaches this shell, which is the
# container's PID 1).
OUT_DIR="$(mktemp -d /tmp/avocado-out.XXXXXX)"
# Created just before the runner starts. Files whose status changed after it are the run's.
START_MARKER=""
# Files larger than this (KiB) are listed in outside-subject/SKIPPED.txt instead of copied.
OUTSIDE_MAX_KB=10240
# Save the files the agent wrote outside the subject copy, such as stubs, harnesses and scripts
# under /tmp, /work or /app/scripts, which would otherwise disappear with the container. They go
# to outside-subject/, keeping their absolute paths (e.g. outside-subject/tmp/work/foo_stub.c).
# Skipped: the subject copy (already saved), /work/out (the publication mount, where this
# function's results are going), the harness's own private directories (this
# script's outputs, the Claude Code config, which holds credentials, and the `claude` wrapper),
# the scratch directories of the harness's own CBMC tools, the package's .venv, dot-directories
# under $HOME (caches), and CBMC *.goto binaries. `-cnewer` uses the status-change time, so a
# file copied with `cp -p` (which keeps an old mtime) still counts.
# shellcheck disable=SC2329
collect_outside_subject() {
  [ -n "$START_MARKER" ] && [ -f "$START_MARKER" ] || return 0
  local dest="$OUT_DIR/outside-subject" small big
  local roots=(/tmp /var/tmp /app /work)
  if [ -n "${HOME:-}" ] && [ -d "$HOME" ] && [ "$HOME" != / ]; then
    roots+=("$HOME")
  fi
  small="$(mktemp /tmp/avocado-outside.XXXXXX)"
  big="$(mktemp /tmp/avocado-outside.XXXXXX)"
  find "${roots[@]}" \
    \( -path "$SUBJECT_DIR" -o -path /work/out -o -path "$OUT_DIR" -o -path "$START_MARKER" \
       -o -path "$small" -o -path "$big" \
       -o -path "${CLAUDE_CONFIG_DIR:-/nonexistent}" -o -path '/tmp/avocado-bin.*' \
       -o -path '/tmp/avocado-cbmc-*' -o -path '/tmp/avocado-compiled-*' \
       -o -path '/tmp/avocado-vacuity-*' -o -path /app/.venv -o -path "${HOME:-/nonexistent}/.*" \) -prune \
    -o -type f -cnewer "$START_MARKER" ! -name '*.goto' -print0 2>/dev/null |
    while IFS= read -r -d '' f; do
      if [ "$(du -k -- "$f" 2>/dev/null | cut -f1)" -le "$OUTSIDE_MAX_KB" ] 2>/dev/null; then
        printf '%s\0' "$f" >>"$small"
      else
        printf '%s\t%s KiB\n' "$f" "$(du -k -- "$f" 2>/dev/null | cut -f1)" >>"$big"
      fi
    done
  if [ -s "$small" ] || [ -s "$big" ]; then
    mkdir -p "$dest"
  fi
  if [ -s "$small" ]; then
    tar --null -T "$small" -cf - 2>/dev/null | tar -C "$dest" -xf - 2>/dev/null || true
  fi
  if [ -s "$big" ]; then
    cp "$big" "$dest/SKIPPED.txt"
  fi
  rm -f "$small" "$big"
}
# shellcheck disable=SC2329
publish() {
  collect_outside_subject || true
  # Save the conversation transcript(s) before the config dir disappears with the container.
  # A project directory holds <session-id>.jsonl for each session and, for a session that
  # started subagents, <session-id>/subagents/*.jsonl; copy all of it.
  if compgen -G "${CLAUDE_CONFIG_DIR:-/nonexistent}/projects/*/*.jsonl" >/dev/null; then
    mkdir -p "$OUT_DIR/transcripts"
    cp -a "$CLAUDE_CONFIG_DIR"/projects/*/. "$OUT_DIR/transcripts/" 2>/dev/null || true
  fi
  if [ "$RUNNER" = claude ]; then
    printf '%s\n' "$TARGET" >"$OUT_DIR/prompt.txt"
  fi
  cp -a "$OUT_DIR/." /work/out/ 2>/dev/null || true
}
trap publish EXIT
# PID 1 ignores a signal it has no handler for, and Bash only runs a handler once the
# foreground command ends. So the handler is installed before any slow step, and each slow
# step (installing the package, the runner) is run in the background and waited on; on
# SIGTERM it is told to stop, and the EXIT trap then publishes what exists.
CHILD_PID=""
# shellcheck disable=SC2329
on_term() {
  if [ -n "$CHILD_PID" ]; then
    kill -TERM "$CHILD_PID" 2>/dev/null || true
    wait "$CHILD_PID" 2>/dev/null || true
  fi
  exit 143
}
trap on_term TERM INT
# Run a command in the background and wait for it; returns its exit code.
run_child() {
  "$@" &
  CHILD_PID=$!
  local rc=0
  wait "$CHILD_PID" || rc=$?
  CHILD_PID=""
  return "$rc"
}

# Install the harness next to the subject, then remove the bootstrap directory. Bash has
# already opened this script, so deleting it mid-run is safe.
cp -a "$BOOT_DIR/harness/." /app/
rm -rf "$BOOT_DIR"

# Install the treatment's Python package (its `avocado-*` tools and, where it has one, the
# `avocado-verify` driver) the way docker/entrypoint.sh does for `make run`. The pure agentic
# baseline ships no package, so this is skipped there.
if [ -f /app/pyproject.toml ] && [ -f /app/uv.lock ]; then
  # --no-dev: the agent needs none of the development dependencies.
  echo "run-isolated: installing the harness package (uv sync --frozen --no-dev)"
  run_child uv --directory /app sync --frozen --no-dev
  export VIRTUAL_ENV=/app/.venv
  export PATH=/app/.venv/bin:$PATH
  # The package can declare commands whose modules the snapshot leaves out (scripts/ is not
  # given to the agent); remove those, so every command on the agent's PATH works.
  python - <<'PY'
import importlib.metadata
import importlib.util
import pathlib

for ep in importlib.metadata.entry_points(group="console_scripts"):
    script = pathlib.Path("/app/.venv/bin", ep.name)
    if not script.exists():
        continue
    try:
        found = importlib.util.find_spec(ep.module) is not None
    except ModuleNotFoundError:
        found = False
    except Exception:  # Some other failure: leave the command alone.
        found = True
    if not found:
        script.unlink()
PY
fi
if [ "$RUNNER" = avocado-verify ] && ! command -v avocado-verify >/dev/null 2>&1; then
  echo "run-isolated: the harness does not provide an avocado-verify command" >&2
  exit 2
fi

# Fresh Claude Code state for every run. `.claude.json` carries onboarding state and
# `.credentials.json` the OAuth login; nothing else (projects/, memory, todos) is copied.
CLAUDE_CONFIG_DIR="$(mktemp -d /tmp/claude-config.XXXXXX)"
export CLAUDE_CONFIG_DIR
if [ -d /auth ]; then
  for f in .credentials.json .claude.json; do
    if [ -f "/auth/$f" ]; then
      cp "/auth/$f" "$CLAUDE_CONFIG_DIR/$f"
    fi
  done
fi
# A fresh config dir has never trusted these directories, so Claude would skip settings
# permission rules and print a warning that corrupts `--output-format json`. Mark both the
# subject directory (the cwd) and the harness trusted, and onboarding done, up front.
jq -n --slurpfile prior <(cat "$CLAUDE_CONFIG_DIR/.claude.json" 2>/dev/null || echo '{}') \
  --arg subject "$SUBJECT_DIR" \
  '$prior[0] * {hasCompletedOnboarding: true,
                projects: {"/app": {hasTrustDialogAccepted: true},
                           ($subject): {hasTrustDialogAccepted: true}}}' \
  >"$CLAUDE_CONFIG_DIR/.claude.json.tmp"
mv "$CLAUDE_CONFIG_DIR/.claude.json.tmp" "$CLAUDE_CONFIG_DIR/.claude.json"

SETTINGS_ARGS=()
if [ -f /app/.claude/settings.json ]; then
  SETTINGS_ARGS=(--settings /app/.claude/settings.json)
fi
# `claude --settings <file>` applies the file's env and permissions but ignores its "model"
# key (verified with Claude Code 2.1.308: the account default is used instead), so the model
# must be passed explicitly. A profile's AVOCADO_MODEL wins; otherwise take the settings
# file's top-level "model". The settings file may not be strict JSON; if jq cannot parse it,
# fall back to the first "model" key that grep finds.
MODEL="${AVOCADO_MODEL:-}"
if [ -z "$MODEL" ] && [ -f /app/.claude/settings.json ]; then
  if ! MODEL="$(jq -r '.model // empty' /app/.claude/settings.json 2>/dev/null)"; then
    # `|| true`: with no "model" key, grep fails, and pipefail would abort the script.
    MODEL="$({ grep -oE '"model"[[:space:]]*:[[:space:]]*"[^"]+"' /app/.claude/settings.json || true; } \
      | head -1 | sed -E 's/.*:[[:space:]]*"([^"]+)"/\1/')"
  fi
fi
MODEL_ARGS=()
if [ -n "$MODEL" ]; then
  MODEL_ARGS=(--model "$MODEL")
fi
REAL_CLAUDE="$(command -v claude)"

echo "run-isolated: $(claude --version) | runner=$RUNNER | cwd=$SUBJECT_DIR | model=${MODEL:-<account default>}"

# Launch from inside the subject copy, as in the manual workflow. Keep going on a non-zero
# exit so the transcripts are still saved.
cd "$SUBJECT_DIR"
case "$RUNNER" in
  claude)
    # /app is added so the agent can read ../AGENTS.md.
    echo "run-isolated: prompt: $TARGET"
    COMMAND=(claude --print "$TARGET"
      --output-format json
      --dangerously-skip-permissions
      --add-dir /app
      "${SETTINGS_ARGS[@]}"
      "${MODEL_ARGS[@]}")
    ;;
  avocado-verify)
    # The driver starts `claude -p` itself, one session per function, with neither --settings
    # nor --model. A wrapper first on PATH appends both to every session it starts, so each
    # session sees the same settings and model as the `claude` runner would. Its log goes to
    # stdout/stderr (run.log on the host) so progress can be followed while it runs; the
    # per-function results land next to the C file as <file>-avocado-verify.jsonl.
    SHIM_DIR="$(mktemp -d /tmp/avocado-bin.XXXXXX)"
    {
      echo '#!/usr/bin/env bash'
      printf 'exec %q "$@"' "$REAL_CLAUDE"
      for arg in "${SETTINGS_ARGS[@]}" "${MODEL_ARGS[@]}"; do
        printf ' %q' "$arg"
      done
      echo
    } >"$SHIM_DIR/claude"
    chmod +x "$SHIM_DIR/claude"
    export PATH="$SHIM_DIR:$PATH"
    echo "run-isolated: file: $TARGET"
    COMMAND=(avocado-verify --file "$TARGET")
    if [ -n "$RESUME" ]; then
      COMMAND+=(--resume-from "${TARGET%.*}-avocado-verify.jsonl")
    fi
    ;;
esac
printf '%q ' "${COMMAND[@]}" >"$OUT_DIR/command.txt"
echo >>"$OUT_DIR/command.txt"
START_MARKER="$(mktemp /tmp/avocado-start.XXXXXX)"
rc=0
if [ "$RUNNER" = claude ]; then
  run_child "${COMMAND[@]}" >"$OUT_DIR/claude-output.json" </dev/null || rc=$?
else
  run_child "${COMMAND[@]}" </dev/null || rc=$?
fi

echo "run-isolated: $RUNNER exited with $rc; result:"
case "$RUNNER" in
  claude)
    jq -r '.result // empty' "$OUT_DIR/claude-output.json" 2>/dev/null | head -c 2000 || true
    ;;
  avocado-verify)
    jq -c 'select(.type == "run_summary")' "${TARGET%.*}-avocado-verify.jsonl" 2>/dev/null \
      | tail -n 1 || true
    ;;
esac
echo
exit "$rc"
