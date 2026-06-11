#!/usr/bin/env bash
set -euo pipefail

cd /app

# Keep the container's virtual environment off the bind-mounted /app so it does
# not collide with a host-built (e.g. macOS) .venv. When backed by a named
# volume, `uv sync --frozen` becomes a fast no-op after the first run.
VENV="${UV_PROJECT_ENVIRONMENT:-/app/.venv}"

if [ -f pyproject.toml ] && [ -f uv.lock ]; then
  uv sync --frozen
fi

export VIRTUAL_ENV="$VENV"
export PATH="$VENV/bin:$PATH"

exec "$@"
