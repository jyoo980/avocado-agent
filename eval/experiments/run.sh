#!/usr/bin/env bash
# Run Avocado Agent on a subject program N times, each in a fresh container.
#
# Every run gets:
#   - its own container (`--rm`), so Claude Code's ~/.claude state, stray CBMC processes and
#     anything written under /app die with it;
#   - its own copy of the subject program, taken from the pristine `--subject` directory and
#     scrubbed of harness artifacts, mounted at /work/subject;
#   - a read-only view of this repository at /src (copied to /app inside the container);
#   - optional per-container memory/CPU limits, so an OOM-killed CBMC only takes down that run.
#
# Results land on the host (default ~/.local/share/avocado-experiments, outside the repository
# and outside every container) under <results>/<tag>/<timestamp>/run-<i>/:
#   subject/            the subject copy, with the agent's specs and all *.jsonl logs
#   avocado-verify.log  stdout+stderr of the run
#   exit-code           the container's exit code
#
# Usage:
#   eval/experiments/run.sh --subject <dir> --file <c-file-relative-to-subject> \
#       --profile <env-file> [-n 5] [-j 1] [--memory 32g] [--cpus 8] [--results <dir>] \
#       [--tag <name>] [--image <image>] [--claude-timeout <sec>]
#
# See eval/experiments/profiles/ for the auth profiles (subscription vs. LiteLLM).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENGINE="${CONTAINER_ENGINE:-$(command -v docker 2>/dev/null || command -v podman 2>/dev/null || true)}"
IMAGE="${IMAGE_NAME:-avocado-agent-container}"
RUNS=5
JOBS=1
MEMORY=""
CPUS=""
# Results live on the host, outside the repository and outside every container, so they survive
# container exit, `make clean`, and re-cloning the harness. Override with --results or
# AVOCADO_RESULTS_DIR.
RESULTS="${AVOCADO_RESULTS_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/avocado-experiments}"
TAG=""
SUBJECT=""
FILE=""
PROFILE=""
CLAUDE_TIMEOUT=""

usage() { sed -n '2,/^set -euo/p' "${BASH_SOURCE[0]}" | grep '^#' | cut -c3-; exit "${1:-0}"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --subject) SUBJECT="$2"; shift 2 ;;
    --file) FILE="$2"; shift 2 ;;
    --profile) PROFILE="$2"; shift 2 ;;
    -n|--runs) RUNS="$2"; shift 2 ;;
    -j|--jobs) JOBS="$2"; shift 2 ;;
    --memory) MEMORY="$2"; shift 2 ;;
    --cpus) CPUS="$2"; shift 2 ;;
    --results) RESULTS="$2"; shift 2 ;;
    --tag) TAG="$2"; shift 2 ;;
    --image) IMAGE="$2"; shift 2 ;;
    --claude-timeout) CLAUDE_TIMEOUT="$2"; shift 2 ;;
    -h|--help) usage 0 ;;
    *) echo "unknown argument: $1" >&2; usage 1 ;;
  esac
done

[ -n "$ENGINE" ] || { echo "no docker or podman found" >&2; exit 1; }
[ -d "$SUBJECT" ] || { echo "--subject must be a directory: '$SUBJECT'" >&2; exit 1; }
[ -f "$SUBJECT/$FILE" ] || { echo "--file must exist under the subject: '$SUBJECT/$FILE'" >&2; exit 1; }
[ -f "$PROFILE" ] || { echo "--profile must be an env file: '$PROFILE'" >&2; exit 1; }
SUBJECT="$(cd "$SUBJECT" && pwd)"
PROFILE="$(cd "$(dirname "$PROFILE")" && pwd)/$(basename "$PROFILE")"
TAG="${TAG:-$(basename "$SUBJECT")-$(basename "${PROFILE%.env}")}"
BATCH_DIR="$RESULTS/$TAG/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$BATCH_DIR"
echo "results: $BATCH_DIR"

# A `claude` login persisted by eval/experiments/login.sh; mounted read-only when present.
AUTH_VOLUME="avocado-claude-auth"
AUTH_MOUNT=()
if "$ENGINE" volume inspect "$AUTH_VOLUME" >/dev/null 2>&1; then
  AUTH_MOUNT=(-v "$AUTH_VOLUME:/auth:ro")
fi

LIMITS=()
[ -n "$MEMORY" ] && LIMITS+=(--memory "$MEMORY" --memory-swap "$MEMORY")
[ -n "$CPUS" ] && LIMITS+=(--cpus "$CPUS")

VERIFY_ARGS=()
[ -n "$CLAUDE_TIMEOUT" ] && VERIFY_ARGS+=(--claude-timeout "$CLAUDE_TIMEOUT")

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
  local name="avocado-$TAG-run$i"
  copy_subject "$run_dir/subject"
  echo "[$TAG] run $i/$RUNS -> $run_dir"
  set +e
  "$ENGINE" run --rm --name "$name" \
    --env-file "$PROFILE" \
    -e PYTHONDONTWRITEBYTECODE=1 \
    -e DISABLE_AUTOUPDATER=1 \
    -e IS_SANDBOX=1 \
    "${LIMITS[@]}" \
    "${AUTH_MOUNT[@]}" \
    -v "avocado-uv-cache:/root/.cache/uv" \
    -v "$REPO_ROOT:/src:ro" \
    -v "$run_dir/subject:/work/subject" \
    --entrypoint /src/docker/run-isolated.sh \
    "$IMAGE" --file "/work/subject/$FILE" "${VERIFY_ARGS[@]}" \
    >"$run_dir/avocado-verify.log" 2>&1
  local rc=$?
  set -e
  echo "$rc" >"$run_dir/exit-code"
  echo "[$TAG] run $i/$RUNS finished with exit code $rc"
}

{
  echo "subject=$SUBJECT"
  echo "file=$FILE"
  echo "profile=$PROFILE"
  echo "image=$IMAGE"
  echo "runs=$RUNS jobs=$JOBS memory=${MEMORY:-unlimited} cpus=${CPUS:-unlimited}"
  echo "harness_commit=$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo unknown)"
} >"$BATCH_DIR/batch-info.txt"

for i in $(seq 1 "$RUNS"); do
  run_one "$i" &
  if [ "$(jobs -rp | wc -l)" -ge "$JOBS" ]; then
    wait -n
  fi
done
wait
echo "done: $BATCH_DIR"
