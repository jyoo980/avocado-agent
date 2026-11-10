#!/usr/bin/env bash
set -euo pipefail

cd /app

# `make run` mounts the host's working directory at /app, and the container runs as root, so every
# file that the session creates there (the .venv, logs, transcripts, call graphs and merged
# sources) would be owned by the host's root, which the host's user can neither modify nor
# delete. On exit, give each such file to the owner of /app. With rootless docker or podman,
# /app is owned by the container's root, which already maps to the host's user, and nothing is
# changed.
# shellcheck disable=SC2329
restore_ownership() {
  local owner
  owner="$(stat -c %u:%g /app)"
  if [ "$(id -u)" -eq 0 ] && [ "${owner%%:*}" -ne 0 ]; then
    find /app -xdev -user 0 -exec chown -h "$owner" {} + 2>/dev/null \
      || echo "entrypoint: could not give some files under /app to $owner" >&2
  fi
}
trap restore_ownership EXIT

if [ -f pyproject.toml ] && [ -f uv.lock ]; then
  uv sync --frozen
fi

export VIRTUAL_ENV=/app/.venv
export PATH=/app/.venv/bin:$PATH

"$@"
