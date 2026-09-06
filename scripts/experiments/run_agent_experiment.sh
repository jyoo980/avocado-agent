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
#   CLAUDE_TIMEOUT      Per-function `claude -p` timeout in seconds, forwarded to
#                       `--claude-timeout` when set.
#   AVOCADO_REPO_ROOT   Checkout whose harness (`avocado-verify`, `avocado-run-cbmc`, `CLAUDE.md`,
#                       `.claude/settings.json`) the run should use. Defaults to the checkout this
#                       script lives in. Point it at a separate `git worktree` (with its own
#                       `.venv` from `uv sync`) to run several experiments concurrently without
#                       sharing a working directory.
#   AVOCADO_DATA_DIR    Where run directories and result files go. Defaults to
#                       `<AVOCADO_REPO_ROOT>/avocado-experimental-data`.
#   AVOCADO_SCORER_ROOT Checkout whose `evaluate_specification_quality.py` scores the resulting
#                       specifications. Defaults to `AVOCADO_REPO_ROOT`. Set it to one checkout for
#                       every arm of an experiment so both arms are scored by the same code: the
#                       metric is identical either way, but a checkout with the parallel scorer
#                       finishes in a fraction of the time.
set -euo pipefail

if [ "$#" -lt 3 ]; then
  echo "usage: $0 <label> <run-id> <benchmark-dir> [<benchmark-dir> ...]" >&2
  exit 2
fi

label="$1"
run_id="$2"
shift 2

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="${AVOCADO_REPO_ROOT:-$(cd "${script_dir}/../.." && pwd)}"
data_dir="${AVOCADO_DATA_DIR:-${repo_root}/avocado-experimental-data}"
scorer_root="${AVOCADO_SCORER_ROOT:-${repo_root}}"
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
# picks up every `stubs/*.c` for *all* later verifications. Left in place, one run's stubs would
# change what the next run of the same arm verifies, so runs would not be independent. Restore the
# harness's tracked state and drop the files an earlier run's agent added before starting this one.
# Only `stubs/` and `scripts/` are cleaned -- never the whole checkout, whose untracked `.venv/`
# the run needs.
git -C "${repo_root}" checkout -- stubs scripts 2>/dev/null || true
git -C "${repo_root}" clean -qfd stubs scripts 2>/dev/null || true

for benchmark_dir in "$@"; do
  benchmark="$(basename "${benchmark_dir}")"
  work_dir="${run_root}/${benchmark}"
  time_log="${data_dir}/${label}-${run_id}-${benchmark}.time"
  "${script_dir}/strip_specs.py" "${benchmark_dir}" "${work_dir}"
  echo "== ${label}/${run_id}/${benchmark}: agent pass $(date -u +%FT%TZ) commit=$(git -C "${repo_root}" rev-parse --short HEAD)" | tee "${time_log}"
  {
    time -p (
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
    time -p "${scorer_root}/eval/mutants/evaluate_specification_quality.py" "${work_dir}" \
      --auto-include --mutation --jsonl "${data_dir}/${label}-${run_id}-${benchmark}.jsonl"
  } 2>>"${time_log}"
  tail -n 3 "${time_log}"
  find "${work_dir}" \( -name '*.goto' -o -name '*__mutant_*.c' \) -delete
done
