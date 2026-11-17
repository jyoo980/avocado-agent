#!/usr/bin/env bash
# Re-score the specifications an agent run left on disk, using this checkout's scorer.
#
# `run_agent_experiment.sh` scores each benchmark as soon as its agent pass finishes, using the
# arm's own checkout. Use this script to score (or re-score) a run afterwards from one checkout, so
# every arm is scored by the same code, or to recover a scoring pass that was interrupted.
#
# Usage:
#   eval/experiments/rescore_run.sh <label> <run-id> <benchmark> [<benchmark> ...]
#
# Reads `<data-dir>/runs/<label>/<run-id>/<benchmark>/` and overwrites
# `<data-dir>/<label>-<run-id>-<benchmark>.jsonl`. AVOCADO_DATA_DIR defaults to this checkout's
# `avocado-experimental-data`. The scorer uses the stubs `run_agent_experiment.sh` saved in
# `<data-dir>/stubs/<label>/<run-id>/<benchmark>/`; for a run saved before that existed, it warns
# and falls back to this checkout's `stubs/`.
set -euo pipefail

if [ "$#" -lt 3 ]; then
  echo "usage: $0 <label> <run-id> <benchmark> [<benchmark> ...]" >&2
  exit 2
fi

label="$1"
run_id="$2"
shift 2

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
data_dir="${AVOCADO_DATA_DIR:-${repo_root}/avocado-experimental-data}"
cd "${repo_root}"
unset VIRTUAL_ENV

for benchmark in "$@"; do
  work_dir="${data_dir}/runs/${label}/${run_id}/${benchmark}"
  jsonl="${data_dir}/${label}-${run_id}-${benchmark}.jsonl"
  find "${work_dir}" \( -name '*.goto' -o -name '*-callgraph.json' -o -name '*__mutant_*' \) -delete
  echo "== rescoring ${label}/${run_id}/${benchmark}"
  # Score against the stubs the run's agent pass left behind, as the original scoring did.
  stubs_dir="${data_dir}/stubs/${label}/${run_id}/${benchmark}"
  if [ -d "${stubs_dir}" ]; then
    export AVOCADO_STUBS_DIR="${stubs_dir}"
  else
    unset AVOCADO_STUBS_DIR
    echo "$0: warning: no saved stubs in ${stubs_dir}; using ${repo_root}/stubs," \
      "which may differ from the stubs the run's agent pass used" >&2
  fi
  # Publish the result only once the scorer succeeds, so a failed or interrupted pass keeps the
  # previous complete file.
  ./eval/mutants/evaluate_specification_quality.py "${work_dir}" --auto-include --mutation \
    --jsonl "${jsonl}.tmp"
  mv -f -- "${jsonl}.tmp" "${jsonl}"
  find "${work_dir}" \( -name '*.goto' -o -name '*__mutant_*' \) -delete
done
