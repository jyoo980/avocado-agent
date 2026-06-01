#!/usr/bin/env bash
# Usage: ./verify.sh <FUNCTION> [callee1 callee2 ...]
# Verifies a single function's CBMC contract using the CLAUDE.md pipeline.
set -u
FUNCTION="$1"
shift || true
REPLACE=""
for c in "$@"; do
  REPLACE="$REPLACE --replace-call-with-contract $c"
done

goto-cc -o "${FUNCTION}.goto" zopfli.c --function "${FUNCTION}" || { echo "goto-cc FAILED"; exit 2; }
goto-instrument --partial-loops --unwind 5 "${FUNCTION}.goto" "${FUNCTION}.goto" || { echo "unwind FAILED"; exit 2; }
goto-instrument $REPLACE --enforce-contract "${FUNCTION}" "${FUNCTION}.goto" "checking-${FUNCTION}-contracts.goto" || { echo "enforce FAILED"; exit 2; }
cbmc "checking-${FUNCTION}-contracts.goto" --function "${FUNCTION}" --depth 100 > "/tmp/cbmc-${FUNCTION}.log" 2>&1
grep -E ": FAILURE$" "/tmp/cbmc-${FUNCTION}.log" | head -30
grep -E "^\*\* |VERIFICATION" "/tmp/cbmc-${FUNCTION}.log"
