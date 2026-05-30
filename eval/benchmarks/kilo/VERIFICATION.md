# CBMC verification of kilo.c

Each function below was specified with CBMC function contracts
(`__CPROVER_requires` / `__CPROVER_assigns` / `__CPROVER_ensures`, plus loop
bounds) and verified **modularly, one function at a time** with CBMC 6.7.1 using
dynamic frame-condition checking (`goto-instrument --dfcc --enforce-contract`).

## How to reproduce

```
./runall.sh          # re-verifies every contracted function, regenerates kilo-log.jsonl
./verify.sh <fn> <harness>   # verify a single function (see kilo-log.jsonl for the env vars)
```

`verify.sh` performs, for one function `F` driven by harness `H`:

1. `goto-cc --function H` – build the GOTO model with `H` as entry point.
2. `goto-instrument --generate-function-body '(free|<syscalls>)'` – give nondet
   stub bodies to `free` and the unmodelled POSIX/terminal calls the function
   reaches (CBMC has no body for them; DFCC would otherwise assert them
   unreachable). Sound over-approximation for memory safety.
3. `goto-instrument --replace-calls malloc:cbmc_malloc realloc:cbmc_realloc`.
4. `goto-instrument --dfcc H --enforce-contract F --replace-call-with-contract
   cbmc_malloc cbmc_realloc [callees]`.
5. `cbmc` – discharge the contract + all built-in safety checks (bounds, pointer,
   overflow, …).

## Verification models added to kilo.c (trusted)

* `__ctype_b_loc` – glibc `is*()` table lookup intrinsic; modelled to return a
  valid classification table so the calls are callable under DFCC.
* `__errno_location` – `errno` lvalue intrinsic; modelled to a real `int`.
* `cbmc_malloc` / `cbmc_realloc` – non-failing allocators whose contract yields a
  fresh, non-NULL object of the requested size. Needed because the DFCC contracts
  library models `malloc`/`realloc` as always-may-fail and cannot name a freshly
  allocated buffer in a pre-state `assigns` clause — which breaks the very common
  "allocate then immediately write" idiom kilo uses with no NULL check.

## Verified functions (19) — all `VERIFICATION SUCCESSFUL`

Pure / leaf: `is_separator`, `editorSyntaxToColor`, `editorFileWasModified`,
`editorRowHasOpenComment`, `abFree`.

Heap / buffer manipulation: `abAppend`, `editorFreeRow`, `editorUpdateSyntax`,
`editorUpdateRow`, `editorRowInsertChar`, `editorRowDelChar`,
`editorRowAppendString`, `editorRowsToString`, `editorDelRow`.

Terminal / system-call layer: `disableRawMode`, `editorAtExit`, `enableRawMode`,
`getCursorPosition`, `getWindowSize`.

### Bounded preconditions

CBMC is a *bounded* model checker. A few contracts intentionally constrain sizes
so that loops fully unwind (the proofs are sound for the stated bounds):

* `editorUpdateRow` is proved for a single-character row (`size == 1`); its
  tab-expansion inner loop uses a modulo and is SAT-exponential in `size`.
* `editorRowInsertChar/DelChar/AppendString` are proved in the regime that drives
  `editorUpdateRow` at `size == 1` (these call it through its contract).
* `editorRowsToString`, `editorDelRow` are proved for `E.numrows == 1`.

## Not covered in this pass

The remaining functions are dominated either by the multi-row global `E.row`
array with cross-row recursion / index bookkeeping (`editorInsertRow`,
`editorInsertChar`, `editorInsertNewline`, `editorDelChar`, `editorMoveCursor`,
`editorRefreshScreen`, `editorFind`, `editorProcessKeypress`), by varargs
(`editorSetStatusMessage`), by file I/O (`editorOpen`, `editorSave`), by
non-terminating poll loops (`editorReadKey`), or have a genuine latent overflow
(`updateWindowSize`'s `E.screenrows -= 2`). They retain their original bodies.
