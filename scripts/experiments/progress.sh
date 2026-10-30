#!/usr/bin/env bash
# Print a one-line-per-function progress summary for every agent run directory given.
#
# Usage:
#   scripts/experiments/progress.sh <RUN_DIR> [<RUN_DIR> ...]
#
# Reads the `<stem>-avocado-verify.jsonl` logs beneath each directory and prints, per function,
# the outcome, session/turn counts, agent seconds, and cost, followed by the run's total.
set -euo pipefail
for d in "$@"; do
  echo "== ${d}"
  find "${d}" -name '*-avocado-verify.jsonl' | sort | while IFS= read -r f; do
    python3 - "$f" <<'PY'
import json, sys
total_s = total_c = 0.0
for line in open(sys.argv[1]):
    r = json.loads(line)
    if "type" in r:
        continue
    secs = sum((s.get("duration_ms") or 0) for s in r["claude"]) / 1000
    turns = sum((s.get("num_turns") or 0) for s in r["claude"])
    total_s += secs; total_c += r.get("total_cost_to_verify_usd") or 0
    print(f"   {r['function']:32s} {r['outcome']:16s} sessions={r['agent_sessions']} turns={turns:3d} "
          f"sec={secs:7.0f} usd={r.get('total_cost_to_verify_usd') or 0:6.2f}")
print(f"   [{sys.argv[1].split('/')[-1]}] total sec={total_s:.0f} usd={total_c:.2f}")
PY
  done
done
date -u
