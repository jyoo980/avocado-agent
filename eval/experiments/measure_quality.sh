#!/usr/bin/env bash
# Measure specification quality (mutation kill score) and harness time for a benchmark.
#
# Runs `eval/mutants/evaluate_specification_quality.py --mutation` over one benchmark directory,
# writing the JSONL record stream to `avocado-experimental-data/<label>-<benchmark>.jsonl`, and a
# header line (date, commit), the `time -p` output (real/user/sys), and a status line to
# `avocado-experimental-data/<label>-<benchmark>.time`.  The evaluation's own log and error output
# go to the terminal.
#
# Usage:
#   eval/experiments/measure_quality.sh <label> <benchmark-dir> [<benchmark-dir> ...]
#
# Example (baseline over the iteration tier):
#   eval/experiments/measure_quality.sh baseline eval/benchmarks/quicksort eval/benchmarks/csv_parser
#
# The benchmark name used in output file names is the directory's basename (e.g. `quicksort`), so
# the benchmark directories must have distinct basenames, and the label must not contain `/`.
# Benchmarks are processed sequentially so timings are not perturbed by one another.  If the
# evaluation of a benchmark fails, its status line says `status=failed`, its JSONL file (if any) is
# renamed to `<label>-<benchmark>.jsonl.failed`, the remaining benchmarks are still measured, and
# the script exits with a non-zero status.  If the evaluation succeeds but writes no mutation
# summary records (as for a benchmark with no CBMC-annotated functions), the status line says
# `status=not-measured`, the JSONL file is renamed to `<label>-<benchmark>.jsonl.not-measured`, and
# the script likewise goes on and exits with a non-zero status.  An interrupt (Ctrl-C) or SIGTERM
# stops the script after the evaluation of the current benchmark exits and the script records its
# status.
#
# Before each benchmark, the script deletes the mutant sources (`*__mutant_*.c`,
# `*__clause_drop_*.c`) in the benchmark directory that git does not track:  an interrupted run
# leaves them behind, and the evaluation would measure them as sources of the benchmark.  After each
# benchmark, and when the script exits early, it deletes the mutant sources, `*-callgraph.json`
# files, and `*-cbmc-runs.jsonl` logs that appeared in the benchmark directory during the
# evaluation.  It keeps those that existed before, such as an agent's log of its CBMC runs, though
# the evaluation appends to such a log.  The evaluation runs in a scratch directory, where CBMC
# writes its `.goto` files; the script deletes the scratch directory when it exits.
set -euo pipefail

if [ "$#" -lt 2 ]; then
  echo "usage: $0 <label> <benchmark-dir> [<benchmark-dir> ...]" >&2
  exit 2
fi

label="$1"
shift
# The label is part of the output file names, so a `/` would put them outside the output directory.
if [[ "${label}" == */* ]]; then
  echo "$0: label must not contain '/': ${label}" >&2
  exit 2
fi

# Resolve the benchmark directories before changing directory, so that relative paths are relative
# to the directory the script was invoked from.
benchmark_dirs=()
# Maps each benchmark name (a directory's basename) to the directory that has it.  Two benchmarks
# with the same name would write the same output files.
declare -A benchmark_name_dirs=()
for benchmark_dir in "$@"; do
  if [ ! -d "${benchmark_dir}" ]; then
    echo "$0: not a directory: ${benchmark_dir}" >&2
    exit 2
  fi
  resolved_dir="$(realpath -- "${benchmark_dir}")"
  benchmark="$(basename "${resolved_dir}")"
  if [ -n "${benchmark_name_dirs["${benchmark}"]+set}" ]; then
    echo "$0: benchmarks ${benchmark_name_dirs["${benchmark}"]} and ${resolved_dir} have the same name: ${benchmark}" >&2
    exit 2
  fi
  benchmark_name_dirs["${benchmark}"]="${resolved_dir}"
  benchmark_dirs+=("${resolved_dir}")
done

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# `uv run --project` uses this checkout's virtual environment, not that of `VIRTUAL_ENV` or of the
# current directory.
unset VIRTUAL_ENV
out_dir="${repo_root}/avocado-experimental-data"
mkdir -p "${out_dir}"
# `--dirty` marks a measurement of uncommitted changes.  The commit is `unknown` if the checkout has
# no git metadata, as in a Docker image built without `.git`.
commit="$(git -C "${repo_root}" describe --always --dirty 2>/dev/null || echo unknown)"

# Prints, NUL-terminated, the files under directory $1 that the evaluation writes beside sources.
find_evaluation_outputs() {
  find "$1" -type f \( -name '*-callgraph.json' -o -name '*-cbmc-runs.jsonl' \
    -o -name '*__mutant_*.c' -o -name '*__clause_drop_*.c' \) -print0
}

# Deletes the mutant sources under directory $1 that git does not track.
remove_leftover_mutants() {
  if git -C "$1" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    # In a pathspec, `*` also matches `/`, so these patterns match in subdirectories too.
    git -C "$1" ls-files -z --others -- '*__mutant_*.c' '*__clause_drop_*.c' \
      | (cd "$1" && xargs -0 -r rm -f --)
  else
    find "$1" -type f \( -name '*__mutant_*.c' -o -name '*__clause_drop_*.c' \) -delete
  fi
}

# The files that `find_evaluation_outputs` printed before the current evaluation started.
declare -A preexisting_outputs=()

# Records the files that `find_evaluation_outputs` prints for directory $1 in `preexisting_outputs`.
record_preexisting_outputs() {
  local file
  preexisting_outputs=()
  while IFS= read -r -d '' file; do
    preexisting_outputs["${file}"]=1
  done < <(find_evaluation_outputs "$1")
}

# Deletes the files that `find_evaluation_outputs` prints for directory $1, except those in
# `preexisting_outputs`.
remove_new_outputs() {
  local file
  while IFS= read -r -d '' file; do
    [ -n "${preexisting_outputs["${file}"]+set}" ] || rm -f -- "${file}"
  done < <(find_evaluation_outputs "$1")
}

# The benchmark directory being measured, if any.
current_benchmark_dir=""
work_dir="$(mktemp -d)"
cleanup() {
  if [ -n "${current_benchmark_dir}" ]; then
    remove_new_outputs "${current_benchmark_dir}"
  fi
  rm -rf -- "${work_dir}"
}
trap cleanup EXIT
# The status with which the script exits because it received a signal:  130 for SIGINT, as from
# Ctrl-C, or 143 for SIGTERM.  Empty if it received neither.  On SIGINT, bash exits only if its
# child dies from SIGINT, but `uv run` instead exits normally with status 130, so without this trap
# the loop would go on to the next benchmark.  Without the SIGTERM trap, bash would exit at once,
# before it records the status of the benchmark.  With these traps, bash runs them only after the
# evaluation exits, so the evaluation's final writes precede the status line and the renaming of the
# JSONL file.  The script does not stop on every status above 128:  137, for example, means that the
# out-of-memory killer killed the evaluation, which is a failure of one benchmark.
signal_status=""
trap 'signal_status=130' INT
trap 'signal_status=143' TERM
# Exits the script if it received a signal.
exit_if_signaled() {
  if [ -n "${signal_status}" ]; then
    echo "$0: stopped by a signal" >&2
    exit "${signal_status}"
  fi
}
cd "${work_dir}"

failed_benchmarks=()
unmeasured_benchmarks=()
for benchmark_dir in "${benchmark_dirs[@]}"; do
  exit_if_signaled
  benchmark="$(basename "${benchmark_dir}")"
  jsonl="${out_dir}/${label}-${benchmark}.jsonl"
  time_log="${out_dir}/${label}-${benchmark}.time"
  rm -f -- "${jsonl}" "${jsonl}.failed" "${jsonl}.not-measured"
  remove_leftover_mutants "${benchmark_dir}"
  record_preexisting_outputs "${benchmark_dir}"
  current_benchmark_dir="${benchmark_dir}"
  echo "== ${label}/${benchmark}: $(date -u +%FT%TZ) commit=${commit}" | tee "${time_log}"
  # `time -p` prints POSIX real/user/sys lines to the group's stderr, which goes to the time log.
  # The evaluation's own stderr goes to the script's stderr, via file descriptor 3.
  status=0
  { time -p uv run --quiet --project "${repo_root}" python3 \
    "${repo_root}/eval/mutants/evaluate_specification_quality.py" "${benchmark_dir}" \
    --auto-include --mutation --jsonl "${jsonl}" 2>&3 3>&-; } 3>&2 2>>"${time_log}" || status=$?
  if [ "${status}" -eq 0 ] \
    && ! grep -q -E '"kind": *"mutation_summary"' "${jsonl}" 2>/dev/null; then
    # `summarize_quality.py` rejects a file with no mutation summary records.
    echo "status=not-measured reason=no mutation summary records" >>"${time_log}"
    if [ -e "${jsonl}" ]; then
      mv -f -- "${jsonl}" "${jsonl}.not-measured"
    fi
    echo "$0: no mutation summary records for ${benchmark_dir}" >&2
    unmeasured_benchmarks+=("${benchmark_dir}")
  elif [ "${status}" -eq 0 ]; then
    echo "status=ok" >>"${time_log}"
  else
    echo "status=failed exit=${status}" >>"${time_log}"
    if [ -e "${jsonl}" ]; then
      mv -f -- "${jsonl}" "${jsonl}.failed"
    fi
    echo "$0: evaluation failed (exit ${status}) for ${benchmark_dir}" >&2
    failed_benchmarks+=("${benchmark_dir}")
  fi
  tail -n +2 "${time_log}"
  remove_new_outputs "${benchmark_dir}"
  current_benchmark_dir=""
  exit_if_signaled
done

if [ "${#failed_benchmarks[@]}" -gt 0 ]; then
  echo "$0: evaluation failed for: ${failed_benchmarks[*]}" >&2
fi
if [ "${#unmeasured_benchmarks[@]}" -gt 0 ]; then
  echo "$0: no mutation summary records for: ${unmeasured_benchmarks[*]}" >&2
fi
if [ "${#failed_benchmarks[@]}" -gt 0 ] || [ "${#unmeasured_benchmarks[@]}" -gt 0 ]; then
  exit 1
fi
