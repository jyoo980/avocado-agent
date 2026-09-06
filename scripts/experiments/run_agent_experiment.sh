#!/usr/bin/env bash
# Run one full `avocado-verify` pass over a benchmark from a spec-less starting point, then score
# the specifications the agent produced.
#
# For each benchmark directory given:
#   1. Copy it to `avocado-experimental-data/runs/<label>/<run-id>/<benchmark>/` with every CBMC
#      contract clause removed (see `strip_specs.py`), so the agent starts from scratch and the
#      committed benchmark files are never touched.
#   2. Run `avocado-verify --file` on every `.c` file in the copy (in sorted order), timing the
#      whole pass with `time -p`. Agent time and cost per function come from the
#      `<stem>-avocado-verify.jsonl` logs that `avocado-verify` writes next to each file.
#   3. Score the resulting specifications with `evaluate_specification_quality.py --mutation`,
#      writing `avocado-experimental-data/<label>-<run-id>-<benchmark>.jsonl`.
#
# Usage:
#   scripts/experiments/run_agent_experiment.sh <label> <run-id> <benchmark-dir> [<benchmark-dir> ...]
#
# Example (first baseline run over the iteration tier):
#   scripts/experiments/run_agent_experiment.sh baseline 1 eval/benchmarks/quicksort eval/benchmarks/csv_parser
#
# Environment:
#   CLAUDE_TIMEOUT   Per-function `claude -p` timeout in seconds, forwarded to `--claude-timeout`
#                    when set.
set -euo pipefail

if [ "$#" -lt 3 ]; then
  echo "usage: $0 <label> <run-id> <benchmark-dir> [<benchmark-dir> ...]" >&2
  exit 2
fi

label="$1"
run_id="$2"
shift 2

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
data_dir="${repo_root}/avocado-experimental-data"
run_root="${data_dir}/runs/${label}/${run_id}"
mkdir -p "${run_root}"

# `claude -p --dangerously-skip-permissions` refuses to run as root unless this is set (README).
export IS_SANDBOX=1

for benchmark_dir in "$@"; do
  benchmark="$(basename "${benchmark_dir}")"
  work_dir="${run_root}/${benchmark}"
  time_log="${data_dir}/${label}-${run_id}-${benchmark}.time"
  "${repo_root}/scripts/experiments/strip_specs.py" "${benchmark_dir}" "${work_dir}"
  echo "== ${label}/${run_id}/${benchmark}: agent pass $(date -u +%FT%TZ) commit=$(git -C "${repo_root}" rev-parse --short HEAD)" | tee "${time_log}"
  {
    time -p (
      cd "${repo_root}"
      while IFS= read -r source_file; do
        echo "-- avocado-verify --file ${source_file} $(date -u +%FT%TZ)"
        avocado_args=(--file "${source_file}")
        if [ -n "${CLAUDE_TIMEOUT:-}" ]; then
          avocado_args+=(--claude-timeout "${CLAUDE_TIMEOUT}")
        fi
        # avocado-verify exits 1 when some function is left unverified; that is a result, not an
        # error, so keep going.
        avocado-verify "${avocado_args[@]}" || echo "-- avocado-verify exited $? for ${source_file}"
      done < <(find "${work_dir}" -name '*.c' -not -name '*__mutant_*' | sort)
    )
  } 2>>"${time_log}"
  tail -n 3 "${time_log}"

  echo "== ${label}/${run_id}/${benchmark}: scoring $(date -u +%FT%TZ)" | tee -a "${time_log}"
  find "${work_dir}" \( -name '*.goto' -o -name '*-callgraph.json' -o -name '*__mutant_*.c' \) -delete
  {
    time -p "${repo_root}/eval/mutants/evaluate_specification_quality.py" "${work_dir}" \
      --auto-include --mutation --jsonl "${data_dir}/${label}-${run_id}-${benchmark}.jsonl"
  } 2>>"${time_log}"
  tail -n 3 "${time_log}"
  find "${work_dir}" \( -name '*.goto' -o -name '*__mutant_*.c' \) -delete
done
