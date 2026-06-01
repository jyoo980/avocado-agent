# CBMC contract verification for `zopfli.c`

This directory contains the CBMC verification harnesses for the function
contracts (`__CPROVER_requires` / `__CPROVER_ensures` / `__CPROVER_assigns`)
inserted into `../zopfli.c`.

## Recipe

Contracts are enforced with CBMC's Dynamic Frame Condition Checking (DFCC).
Because most functions in `zopfli.c` are `static`, each harness `#include`s the
whole translation unit so the static symbol is visible, and calls the target
once so DFCC can wrap it in CHECK mode:

```c
#include "zopfli.c"
void harness(void) { /* nondet args */ TARGET(...); }
```

The verification pipeline for a function `FN` (run from this `c_code/`
directory) is:

```sh
goto-cc --function harness -I. -o /tmp/FN.goto cbmc/h_FN.c
goto-instrument --generate-function-body 'free|malloc' /tmp/FN.goto /tmp/FN.gb.goto
goto-instrument --dfcc harness --enforce-contract FN /tmp/FN.gb.goto /tmp/FN.chk.goto
cbmc /tmp/FN.chk.goto
```

The exact command for every verified function is recorded in
`../zopfli-log.jsonl`. `cbmc/verify.sh FN` runs the same pipeline (set
`APPLY_LOOP_CONTRACTS=1` for functions that carry loop contracts).

### Notes on the toolchain

* `--generate-function-body 'free|malloc'` is required: the DFCC contracts
  library inlines `free`, and `zopfli.c`'s own use of `malloc`/`free` otherwise
  leaves those symbols body-less at instrument time. This step links CBMC's
  real `malloc` model and an empty `free`.
* The entry point must be baked in at `goto-cc` time (`--function harness`),
  otherwise the DFCC nondet-static-initialization pass aborts.
* `--apply-loop-contracts` is **opt-in**: enabling it globally makes DFCC reject
  writes to the index variable of any loop that has no explicit loop contract.

## GCC build

The `__CPROVER_*` contract clauses sit in declarator position and are only
understood by the CBMC front end (`goto-cc`); a plain `gcc` build of
`zopfli.c` no longer compiles. This is inherent to inline function contracts
and expected for a CBMC verification target.
