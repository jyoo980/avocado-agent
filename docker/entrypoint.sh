#!/usr/bin/env bash
set -euo pipefail

cd /app

if [ -f pyproject.toml ] && [ -f uv.lock ]; then
  uv sync --frozen
fi

export VIRTUAL_ENV=/app/.venv
export PATH=/app/.venv/bin:$PATH

exec "$@"
