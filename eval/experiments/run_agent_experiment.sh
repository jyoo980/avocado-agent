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
#   eval/experiments/run_agent_experiment.sh <label> <run-id> <benchmark-dir> [<benchmark-dir> ...]
#
# Example (first baseline run over the iteration tier):
#   eval/experiments/run_agent_experiment.sh baseline 1 eval/benchmarks/quicksort eval/benchmarks/csv_parser
#
# Environment:
#   CLAUDE_TIMEOUT      Per-function `claude -p` timeout in seconds, forwarded to
#                       `--claude-timeout` when set.
#   AVOCADO_JOBS        Forwarded to `avocado-verify --jobs` when set: how many functions of one
#                       file are specified concurrently. When it is unset, `avocado-verify`'s own
#                       default applies. Set it to 1 for a sequential run.
#   AVOCADO_REPO_ROOT   Checkout whose harness (`avocado-verify`, `avocado-run-cbmc`, `CLAUDE.md`,
#                       `.claude/settings.json`) the run should use. Defaults to the checkout this
#                       script lives in. Point it at a separate `git worktree` (with its own
#                       `.venv` from `uv sync`) to run several experiments concurrently without
#                       sharing a working directory.
#   AVOCADO_DATA_DIR    Where run directories and result files go. Defaults to
#                       `<AVOCADO_REPO_ROOT>/avocado-experimental-data`.
#   AVOCADO_SKIP_GLOB   Shell pattern; any `.c` file whose path matches it is removed from the
#                       working copy, so neither the agent pass nor the scoring pass sees it. Use
#                       it to keep a benchmark's vendored third-party sources out of a run, e.g.
#                       `*/polarssl/*` for mkey, whose committed specifications cover only its own
#                       three source files.
#   AVOCADO_SCORER_ROOT Checkout whose `evaluate_specification_quality.py` scores the resulting
#                       specifications. Defaults to `AVOCADO_REPO_ROOT`. Set it to one checkout for
#                       every arm of an experiment so both arms are scored by the same code: the
#                       metric is identical either way, but a checkout with the parallel scorer
#                       finishes in a fraction of the time.
#   AVOCADO_DISCARD_HARNESS_CHANGES
#                       Set to 1 to let the run discard uncommitted changes in the harness
#                       checkout's `stubs/` and `scripts/` (see `reset_harness` below). Otherwise the
#                       script refuses to start when there are any.
#
# The script stops, without scoring the benchmark, if `avocado-verify` fails to complete its pass
# over a file (see `verify_completed` below).
set -euo pipefail

if [ "$#" -lt 3 ]; then
  echo "usage: $0 <label> <run-id> <benchmark-dir> [<benchmark-dir> ...]" >&2
  exit 2
fi

label="$1"
run_id="$2"
shift 2

# Resolve every path to an absolute one before the `cd "${repo_root}"` below, so that relative
# paths given by the caller keep their caller-relative meaning.
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(realpath -- "${AVOCADO_REPO_ROOT:-${script_dir}/../..}")"
data_dir="${AVOCADO_DATA_DIR:-${repo_root}/avocado-experimental-data}"
mkdir -p "${data_dir}"
data_dir="$(realpath -- "${data_dir}")"
scorer_root="$(realpath -- "${AVOCADO_SCORER_ROOT:-${repo_root}}")"
benchmark_dirs=()
for benchmark_dir in "$@"; do
  benchmark_dirs+=("$(realpath -- "${benchmark_dir}")")
done
run_root="${data_dir}/runs/${label}/${run_id}"
mkdir -p "${run_root}"

# Use the chosen checkout's tools (both for the harness and for the `avocado-run-cbmc` the inner
# agent finds on PATH), and let `uv run` shebangs resolve that checkout's project.
export PATH="${repo_root}/.venv/bin:${PATH}"
unset VIRTUAL_ENV
cd "${repo_root}"

# `claude -p --dangerously-skip-permissions` refuses to run as root unless this is set (README).
export IS_SANDBOX=1

# CLAUDE.md lets the inner agent add CBMC stub files to the harness checkout, and `build_stub_index`
# picks up every `stubs/*.c` for *all* later verifications. Left in place, the stubs the agent
# added for one benchmark would change what a later benchmark, or a later run, verifies, so
# results would depend on what ran before. The script therefore restores the harness's tracked
# `stubs/` and `scripts/` before each benchmark and after the last one. It cleans only those two
# directories -- never the whole checkout, whose untracked `.venv/` the run needs.
reset_harness() {
  git -C "${repo_root}" checkout -- stubs scripts
  git -C "${repo_root}" clean -qfd stubs scripts
}

# The reset discards every uncommitted change in `stubs/` and `scripts/`, so refuse to start if
# there are any, unless the caller opts in (e.g. to clean up after an interrupted run).
if [ -n "$(git -C "${repo_root}" status --porcelain -- stubs scripts)" ] \
  && [ "${AVOCADO_DISCARD_HARNESS_CHANGES:-}" != 1 ]; then
  echo "$0: ${repo_root} has uncommitted changes in stubs/ or scripts/:" >&2
  git -C "${repo_root}" status --short -- stubs scripts >&2
  echo "$0: the run would discard them. Commit or remove them, or set" \
    "AVOCADO_DISCARD_HARNESS_CHANGES=1 to discard them." >&2
  exit 1
fi

# Succeeds if `avocado-verify` exited with status $1 after finishing its run over file $2: either
# every function verified (0), or it wrote its terminal "completed" run summary and some function
# is left unverified (1). Any other outcome -- a missing executable, a usage error, a crash, or a
# stop on a usage limit -- means the pass did not run to completion.
verify_completed() {
  local status="$1" log="${2%.c}-avocado-verify.jsonl"
  [ "${status}" -eq 0 ] && return 0
  [ "${status}" -eq 1 ] && [ -f "${log}" ] \
    && tail -n 1 "${log}" | grep -q '"type": *"run_summary"' \
    && tail -n 1 "${log}" | grep -q '"status": *"completed"'
}

for benchmark_dir in "${benchmark_dirs[@]}"; do
  benchmark="$(basename "${benchmark_dir}")"
  reset_harness
  work_dir="${run_root}/${benchmark}"
  time_log="${data_dir}/${label}-${run_id}-${benchmark}.time"
  "${script_dir}/strip_specs.py" "${benchmark_dir}" "${work_dir}"
  if [ -n "${AVOCADO_SKIP_GLOB:-}" ]; then
    find "${work_dir}" -name '*.c' -path "${AVOCADO_SKIP_GLOB}" -delete
  fi
  echo "== ${label}/${run_id}/${benchmark}: agent pass $(date -u +%FT%TZ) commit=$(git -C "${repo_root}" rev-parse --short HEAD)" | tee "${time_log}"
  {
    time -p (
      while IFS= read -r source_file; do
        echo "-- avocado-verify --file ${source_file} $(date -u +%FT%TZ)"
        avocado_args=(--file "${source_file}")
        if [ -n "${CLAUDE_TIMEOUT:-}" ]; then
          avocado_args+=(--claude-timeout "${CLAUDE_TIMEOUT}")
        fi
        if [ -n "${AVOCADO_JOBS:-}" ]; then
          avocado_args+=(--jobs "${AVOCADO_JOBS}")
        fi
        # avocado-verify exits 1 when some function is left unverified; that is a result, not an
        # error, so keep going. Any failure to complete the pass stops the run before scoring, so
        # the arm is not scored as if it had verified nothing.
        status=0
        avocado-verify "${avocado_args[@]}" || status=$?
        if ! verify_completed "${status}" "${source_file}"; then
          message="$0: avocado-verify failed (exit ${status}) for ${source_file}; not scoring"
          message+=" ${label}/${run_id}/${benchmark}"
          echo "${message}"       # to the terminal
          echo "${message}" >&2   # to the time log
          exit 1
        fi
        if [ "${status}" -ne 0 ]; then
          echo "-- avocado-verify exited ${status} for ${source_file}"
        fi
      done < <(find "${work_dir}" -name '*.c' -not -name '*__mutant_*' | sort)
    )
  } 2>>"${time_log}"
  tail -n 3 "${time_log}"

  echo "== ${label}/${run_id}/${benchmark}: scoring $(date -u +%FT%TZ)" | tee -a "${time_log}"
  find "${work_dir}" \( -name '*.goto' -o -name '*-callgraph.json' -o -name '*__mutant_*.c' \) -delete
  # Write to a temporary file and publish it only once the scorer succeeds, so a failed or
  # interrupted scoring pass never leaves a partial result under the final name.
  jsonl="${data_dir}/${label}-${run_id}-${benchmark}.jsonl"
  {
    time -p "${scorer_root}/eval/mutants/evaluate_specification_quality.py" "${work_dir}" \
      --auto-include --mutation --jsonl "${jsonl}.tmp"
  } 2>>"${time_log}"
  mv -f -- "${jsonl}.tmp" "${jsonl}"
  tail -n 3 "${time_log}"
  find "${work_dir}" \( -name '*.goto' -o -name '*__mutant_*.c' \) -delete
done
reset_harness
