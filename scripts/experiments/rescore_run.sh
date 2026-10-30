#!/usr/bin/env bash
# Re-score the specifications an agent run left on disk, using this checkout's scorer.
#
# `run_agent_experiment.sh` scores each benchmark as soon as its agent pass finishes, using the
# arm's own checkout. Use this script to score (or re-score) a run afterwards from one checkout, so
# every arm is scored by the same code, or to recover a scoring pass that was interrupted.
#
# Usage:
#   scripts/experiments/rescore_run.sh <label> <run-id> <benchmark> [<benchmark> ...]
#
# Reads `<data-dir>/runs/<label>/<run-id>/<benchmark>/` and overwrites
# `<data-dir>/<label>-<run-id>-<benchmark>.jsonl`. AVOCADO_DATA_DIR defaults to this checkout's
# `avocado-experimental-data`.
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
  ./eval/mutants/evaluate_specification_quality.py "${work_dir}" --auto-include --mutation \
    --jsonl "${jsonl}"
  find "${work_dir}" \( -name '*.goto' -o -name '*__mutant_*' \) -delete
done
