#!/usr/bin/env bash
# Measure specification quality (mutation kill score) and harness time for a benchmark.
#
# Runs `eval/mutants/evaluate_specification_quality.py --mutation` over one benchmark directory,
# writing the JSONL record stream to `avocado-experimental-data/<label>-<benchmark>.jsonl` and the
# GNU `time` output (wall/user/sys) to `avocado-experimental-data/<label>-<benchmark>.time`.
#
# Usage:
#   scripts/experiments/measure_quality.sh <label> <benchmark-dir> [<benchmark-dir> ...]
#
# Example (baseline over the iteration tier):
#   scripts/experiments/measure_quality.sh baseline eval/benchmarks/quicksort eval/benchmarks/csv_parser
#
# The benchmark name used in output file names is the directory's basename (e.g. `quicksort`).
# Benchmarks are processed sequentially so timings are not perturbed by one another. CBMC artifacts
# (`*.goto`, `*-callgraph.json`, `*-cbmc-runs.jsonl`) are removed before each benchmark so a stale
# cached call graph never leaks into a measurement.
set -euo pipefail

if [ "$#" -lt 2 ]; then
  echo "usage: $0 <label> <benchmark-dir> [<benchmark-dir> ...]" >&2
  exit 2
fi

label="$1"
shift

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# Run from the repository root so `uv run` (used by the Python scripts' shebangs) resolves this
# checkout's project and virtual environment, not that of whatever directory we were invoked from.
cd "${repo_root}"
unset VIRTUAL_ENV
out_dir="${repo_root}/avocado-experimental-data"
mkdir -p "${out_dir}"

for benchmark_dir in "$@"; do
  benchmark="$(basename "${benchmark_dir}")"
  jsonl="${out_dir}/${label}-${benchmark}.jsonl"
  time_log="${out_dir}/${label}-${benchmark}.time"
  find "${benchmark_dir}" \( -name '*.goto' -o -name '*-callgraph.json' -o -name '*-cbmc-runs.jsonl' \
    -o -name '*__mutant_*.c' -o -name '*__clause_drop_*.c' \) -delete
  echo "== ${label}/${benchmark}: $(date -u +%FT%TZ) commit=$(git -C "${repo_root}" rev-parse --short HEAD)" | tee "${time_log}"
  # `time -p` prints POSIX real/user/sys lines to stderr; capture them alongside the log.
  {
    time -p "${repo_root}/eval/mutants/evaluate_specification_quality.py" "${benchmark_dir}" \
      --auto-include --mutation --jsonl "${jsonl}"
  } 2>>"${time_log}"
  tail -n 3 "${time_log}"
  find "${benchmark_dir}" \( -name '*.goto' -o -name '*-callgraph.json' -o -name '*-cbmc-runs.jsonl' \
    -o -name '*__mutant_*.c' -o -name '*__clause_drop_*.c' \) -delete
done
