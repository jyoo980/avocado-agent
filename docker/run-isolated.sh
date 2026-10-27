#!/usr/bin/env bash

# Run `avocado-verify` in a throwaway environment inside a container.
#
# Invoked by `eval/experiments/run.sh` as the container command. It expects:
#   /src          read-only bind mount of the avocado-agent repository
#   /work/subject read-write bind mount of a fresh copy of a subject program
#   /auth         (optional) read-only volume holding a `claude` login
#
# It copies the repository into the container-private `/app`, points Claude Code at an empty
# config directory (so no session transcripts or memory survive between runs), seeds that
# directory with credentials from `/auth` if present, then runs `avocado-verify` with the given
# arguments. If `AVOCADO_MODEL` is set (e.g., by a LiteLLM profile), it is forwarded as `--model`.
set -euo pipefail

if [ ! -f /src/pyproject.toml ]; then
  echo "run-isolated: expected the avocado-agent repository at /src" >&2
  exit 2
fi

# Copy the harness into the (anonymous, --rm'd) /app volume so nothing written there persists.
tar -C /src --exclude=.git --exclude=.venv --exclude='*.env' -cf - . | tar -C /app -xf -
cd /app
uv sync --frozen --quiet
export VIRTUAL_ENV=/app/.venv
export PATH=/app/.venv/bin:$PATH

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
# A fresh config dir has never trusted /app, so Claude would skip .claude/settings.json's
# permission rules and print a warning that corrupts `--output-format json`. Mark the workspace
# trusted and onboarding done up front (merging into any seeded .claude.json).
jq -n --slurpfile prior <(cat "$CLAUDE_CONFIG_DIR/.claude.json" 2>/dev/null || echo '{}') \
  '$prior[0] * {hasCompletedOnboarding: true, projects: {"/app": {hasTrustDialogAccepted: true}}}' \
  >"$CLAUDE_CONFIG_DIR/.claude.json.tmp"
mv "$CLAUDE_CONFIG_DIR/.claude.json.tmp" "$CLAUDE_CONFIG_DIR/.claude.json"

if [ -n "${AVOCADO_MODEL:-}" ]; then
  set -- "$@" --model "$AVOCADO_MODEL"
fi

echo "run-isolated: $(claude --version) | CLAUDE_CONFIG_DIR=$CLAUDE_CONFIG_DIR | model=${AVOCADO_MODEL:-<settings default>}"
exec avocado-verify "$@"
