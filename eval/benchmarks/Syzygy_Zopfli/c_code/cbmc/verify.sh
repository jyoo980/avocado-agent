#!/usr/bin/env bash
# Usage: verify.sh <FUNCTION>
# Verifies the DFCC contract of <FUNCTION> using harness cbmc/h_<FUNCTION>.c
set -uo pipefail
FN="$1"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
H="$DIR/cbmc/h_${FN}.c"
T="$(mktemp -d)"
goto-cc --function harness -I"$DIR" -o "$T/a.goto" "$H" 2>"$T/cc.log" || { echo "GOTO-CC FAIL"; cat "$T/cc.log"; exit 2; }
goto-instrument --generate-function-body 'free|malloc' "$T/a.goto" "$T/b.goto" >"$T/gb.log" 2>&1 || { echo "GB FAIL"; tail "$T/gb.log"; exit 2; }
# Loop contracts are opt-in (APPLY_LOOP_CONTRACTS=1): enabling them globally
# makes DFCC reject writes to the index variable of any loop that has no
# explicit loop contract, so only pass it for functions that use one.
LC=""
[ "${APPLY_LOOP_CONTRACTS:-0}" = "1" ] && LC="--apply-loop-contracts"
goto-instrument $LC --dfcc harness --enforce-contract "$FN" "$T/b.goto" "$T/c.goto" >"$T/in.log" 2>&1 || { echo "INSTRUMENT FAIL"; tail "$T/in.log"; exit 2; }
cbmc "${@:2}" "$T/c.goto"
