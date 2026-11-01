#!/usr/bin/env bash
# Measure specification quality (mutation kill score) and harness time for a benchmark.
#
# Runs `eval/mutants/evaluate_specification_quality.py --mutation` over one benchmark directory,
# writing the JSONL record stream to `avocado-experimental-data/<label>-<benchmark>.jsonl`, and a
# header line (date, commit) plus the `time -p` output (real/user/sys) to
# `avocado-experimental-data/<label>-<benchmark>.time`.  The evaluation's own log and error output
# go to the terminal.
#
# Usage:
#   eval/experiments/measure_quality.sh <label> <benchmark-dir> [<benchmark-dir> ...]
#
# Example (baseline over the iteration tier):
#   eval/experiments/measure_quality.sh baseline eval/benchmarks/quicksort eval/benchmarks/csv_parser
#
# The benchmark name used in output file names is the directory's basename (e.g. `quicksort`).
# Benchmarks are processed sequentially so timings are not perturbed by one another.  If the
# evaluation of a benchmark fails, the remaining benchmarks are still measured, and the script exits
# with a non-zero status.
#
# Before and after each benchmark, and when the script exits early, the script deletes the files
# that the evaluation writes into the benchmark directory (`*.goto`, `*-callgraph.json`,
# `*-cbmc-runs.jsonl`, and mutant sources).  A mutant source that an interrupted run left behind
# would otherwise be measured as one of the benchmark's sources.  Files that git tracks are kept.
set -euo pipefail

if [ "$#" -lt 2 ]; then
  echo "usage: $0 <label> <benchmark-dir> [<benchmark-dir> ...]" >&2
  exit 2
fi

label="$1"
shift

# Resolve the benchmark directories before changing directory, so that relative paths are relative
# to the directory the script was invoked from.
benchmark_dirs=()
for benchmark_dir in "$@"; do
  if [ ! -d "${benchmark_dir}" ]; then
    echo "$0: not a directory: ${benchmark_dir}" >&2
    exit 2
  fi
  benchmark_dirs+=("$(realpath -- "${benchmark_dir}")")
done

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# Run from the repository root so `uv run` (used by the Python scripts' shebangs) resolves this
# checkout's project and virtual environment, not that of whatever directory we were invoked from.
cd "${repo_root}"
unset VIRTUAL_ENV
out_dir="${repo_root}/avocado-experimental-data"
mkdir -p "${out_dir}"
# `--dirty` marks a measurement of uncommitted changes.
commit="$(git -C "${repo_root}" describe --always --dirty)"

# Deletes the files that the evaluation writes into directory $1, except files that git tracks.
remove_evaluation_artifacts() {
  local file
  find "$1" -type f \( -name '*.goto' -o -name '*-callgraph.json' -o -name '*-cbmc-runs.jsonl' \
    -o -name '*__mutant_*.c' -o -name '*__clause_drop_*.c' \) -print0 \
    | while IFS= read -r -d '' file; do
      if ! git -C "$(dirname "${file}")" ls-files --error-unmatch -- "$(basename "${file}")" \
        >/dev/null 2>&1; then
        rm -f -- "${file}"
      fi
    done
}

# The benchmark directory being measured, if any; the EXIT trap cleans it up.
current_benchmark_dir=""
trap '[ -z "${current_benchmark_dir}" ] || remove_evaluation_artifacts "${current_benchmark_dir}"' EXIT

failed_benchmarks=()
for benchmark_dir in "${benchmark_dirs[@]}"; do
  benchmark="$(basename "${benchmark_dir}")"
  jsonl="${out_dir}/${label}-${benchmark}.jsonl"
  time_log="${out_dir}/${label}-${benchmark}.time"
  current_benchmark_dir="${benchmark_dir}"
  remove_evaluation_artifacts "${benchmark_dir}"
  echo "== ${label}/${benchmark}: $(date -u +%FT%TZ) commit=${commit}" | tee "${time_log}"
  # `time -p` prints POSIX real/user/sys lines to the group's stderr, which goes to the time log.
  # The evaluation's own stderr goes to the script's stderr, via file descriptor 3.
  if ! { time -p "${repo_root}/eval/mutants/evaluate_specification_quality.py" "${benchmark_dir}" \
    --auto-include --mutation --jsonl "${jsonl}" 2>&3 3>&-; } 3>&2 2>>"${time_log}"; then
    echo "$0: evaluation failed for ${benchmark_dir}" >&2
    failed_benchmarks+=("${benchmark_dir}")
  fi
  tail -n 3 "${time_log}"
  remove_evaluation_artifacts "${benchmark_dir}"
  current_benchmark_dir=""
done

if [ "${#failed_benchmarks[@]}" -gt 0 ]; then
  echo "$0: evaluation failed for: ${failed_benchmarks[*]}" >&2
  exit 1
fi
