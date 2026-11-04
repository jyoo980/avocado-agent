#!/usr/bin/env bash

# Log in to Claude once, for use by every experiment container started by run.sh.
#
# Two modes:
#   token (default)  Runs `claude setup-token` in a container and prints a long-lived OAuth
#                    token. Paste it into eval/experiments/profiles/subscription.env as
#                    CLAUDE_CODE_OAUTH_TOKEN. Nothing is stored on disk by this script.
#   volume           Runs `claude auth login` with CLAUDE_CONFIG_DIR on a named volume
#                    (avocado-claude-auth). run.sh mounts that volume read-only at /auth and
#                    each run copies just the credentials out of it into a fresh config dir.
#
# Usage: eval/experiments/login.sh [token|volume]
set -euo pipefail

ENGINE="${CONTAINER_ENGINE:-$(command -v docker 2>/dev/null || command -v podman 2>/dev/null || true)}"
IMAGE="${IMAGE_NAME:-avocado-agent-container}"
MODE="${1:-token}"

case "$MODE" in
  token)
    exec "$ENGINE" run -it --rm --entrypoint claude "$IMAGE" setup-token
    ;;
  volume)
    exec "$ENGINE" run -it --rm --entrypoint claude \
      -v avocado-claude-auth:/auth -e CLAUDE_CONFIG_DIR=/auth \
      "$IMAGE" auth login
    ;;
  *)
    echo "usage: $0 [token|volume]" >&2
    exit 1
    ;;
esac
