#!/usr/bin/env bash
# Usage: STUBS="<extra externals>" GI="<extra goto-instrument dfcc flags>" \
#        CB="<extra cbmc flags>" verify.sh <function> <harness>
#
# Verifies a single kilo.c function modularly against its CBMC contract using
# DFCC (--enforce-contract).  Supporting transformations:
#   * free() and the unmodelled POSIX/terminal system calls the function reaches
#     are given non-deterministic stub bodies: CBMC has no GOTO body for them,
#     and DFCC would otherwise turn the missing body into an "unreachable"
#     assertion.  A nondet stub is a sound over-approximation for the
#     memory-safety properties we verify (the calls do not write kilo's buffers,
#     except read(), whose unwritten buffer simply stays non-deterministic).
#   * malloc()/realloc() are redirected to the non-failing modelled allocators
#     cbmc_malloc/cbmc_realloc (see kilo.c) and replaced by their contracts, so
#     unchecked allocations and writes into freshly allocated buffers verify.
# STUBS adds externals (regex alternatives) to stub; GI/CB carry extra
# goto-instrument / cbmc flags (e.g. --replace-call-with-contract g, --unwind N).
set -u
F="$1"; H="$2"
GI="${GI:-}"; CB="${CB:-}"; STUBS="${STUBS:-}"
STUBRE="free"
[ -n "$STUBS" ] && STUBRE="free|$STUBS"
goto-cc --function "$H" -o /tmp/k.goto kilo.c 2>/tmp/gc.err \
  || { echo "GOTO-CC FAIL"; cat /tmp/gc.err; exit 2; }
goto-instrument --generate-function-body "($STUBRE)" /tmp/k.goto /tmp/kb.goto 2>/tmp/gb.err \
  || { echo "STUB FAIL"; tail -20 /tmp/gb.err; exit 4; }
goto-instrument --replace-calls 'malloc:cbmc_malloc' --replace-calls 'realloc:cbmc_realloc' \
  /tmp/kb.goto /tmp/kr.goto 2>/tmp/gr.err \
  || { echo "REPLACE FAIL"; tail -20 /tmp/gr.err; exit 5; }
goto-instrument --dfcc "$H" --enforce-contract "$F" \
  --replace-call-with-contract cbmc_malloc --replace-call-with-contract cbmc_realloc \
  $GI /tmp/kr.goto /tmp/k2.goto 2>/tmp/gi.err \
  || { echo "INSTRUMENT FAIL"; tail -20 /tmp/gi.err; exit 3; }
cbmc $CB /tmp/k2.goto 2>&1 | grep -E "VERIFICATION|: FAILURE|\*\* [0-9]" | head -20
