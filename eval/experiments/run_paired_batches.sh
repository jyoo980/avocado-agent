#!/usr/bin/env bash
# Run paired baseline/treatment agent experiments, one batch of two runs at a time.
#
# Each batch starts one run of each arm at the same moment and waits for both before starting the
# next batch. Pairing matters: a run's wall-clock depends heavily on how much CBMC work is
# competing with it, so the two arms must share the machine. Running only two agent sessions at a
# time also keeps a pass inside the account's usage limit, which a wider fan-out exhausted.
#
# Usage:
#   eval/experiments/run_paired_batches.sh <label>:<checkout>[:<jobs>] <label>:<checkout>[:<jobs>] \
#       -- <benchmark-dir> [<benchmark-dir> ...] -- <run-id> [<run-id> ...]
#
# Each <checkout> is a git worktree of this repository, synced with `uv sync --frozen`, pinned to
# the commit that arm is testing. An optional third field sets `avocado-verify --jobs` for that
# arm, so two arms can be the same commit differing only in how many functions run concurrently. Results land in AVOCADO_DATA_DIR (default: this checkout's
# `avocado-experimental-data`), and every arm is scored by AVOCADO_SCORER_ROOT (default: this
# checkout) -- but see the note in that variable's documentation in run_agent_experiment.sh.
#
# Example (two arms over two benchmarks, runs 1, 2 and 3 of each):
#   eval/experiments/run_paired_batches.sh base:/path/to/base-worktree new:/path/to/new-worktree \
#       -- eval/benchmarks/quicksort eval/benchmarks/csv_parser -- 1 2 3
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/../.." && pwd)"
data="${AVOCADO_DATA_DIR:-${repo_root}/avocado-experimental-data}"

arms=()
benchmarks=()
run_ids=()
section=arms
for arg in "$@"; do
  if [ "${arg}" = "--" ]; then
    case "${section}" in
      arms) section=benchmarks ;;
      benchmarks) section=run_ids ;;
      *) echo "too many -- separators" >&2; exit 2 ;;
    esac
    continue
  fi
  case "${section}" in
    arms) arms+=("${arg}") ;;
    benchmarks) benchmarks+=("${arg}") ;;
    run_ids) run_ids+=("${arg}") ;;
  esac
done

if [ "${#arms[@]}" -lt 2 ] || [ "${#benchmarks[@]}" -lt 1 ] || [ "${#run_ids[@]}" -lt 1 ]; then
  echo "usage: $0 <label>:<checkout> <label>:<checkout> -- <benchmark-dir>... -- <run-id>..." >&2
  exit 2
fi

mkdir -p "${data}"
for id in "${run_ids[@]}"; do
  for arm in "${arms[@]}"; do
    IFS=: read -r label checkout jobs <<<"${arm}"
    AVOCADO_REPO_ROOT="${checkout}" AVOCADO_DATA_DIR="${data}" AVOCADO_JOBS="${jobs:-}" \
      AVOCADO_SCORER_ROOT="${AVOCADO_SCORER_ROOT:-${repo_root}}" \
      "${script_dir}/run_agent_experiment.sh" "${label}" "${id}" "${benchmarks[@]}" \
      > "${data}/${label}-${id}-run.log" 2>&1 &
  done
  wait
  echo "batch ${id} finished $(date -u +%FT%TZ)"
done
