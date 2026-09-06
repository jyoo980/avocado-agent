# Findings

Hypotheses for improving Avocado Agent, with their status. See `WORK_SO_FAR.md` for the measured
entries. Terminal states: confirmed, refuted, noise.

## Parallelize evaluation across functions (and files)

- **Hypothesis:** `evaluate_specification_quality.py` scores functions one at a time; mutants of a
  function already run in a thread pool, but the baseline verification of each function and the
  per-function mutant batches are serialized. On a 128-core machine, scoring several functions
  concurrently (each in its own scratch workspace so mutant file names cannot collide) should cut
  harness time for the evaluation pass by a large factor without changing any score.
- **Axis:** harness time
- **Status:** confirmed
- **Evidence:** WORK_SO_FAR.md entry "Parallel evaluation, scratch directories, per-mutant
  feedback budget". Identical scores on every tier; wall-clock 5.5→2.9 s (quicksort), 6.9→3.7 s
  (csv_parser), 29.7→5.2 s (mkey), 1103→414 s (kilo).
- **Commit:** a0620ca

## Raise the mutant worker cap

- **Hypothesis:** `_MAX_MUTATION_WORKERS = 32` caps concurrency below the machine's 128 cores; a
  function with >32 mutants (e.g. `hexdump`: 42, `mkey_generate_v3_v4`: 33) waits on a second wave.
  Bounding by `os.cpu_count()` alone is a free speedup on big machines and a no-op elsewhere.
- **Axis:** harness time
- **Status:** confirmed (folded into the parallel-evaluation change; a process-wide subprocess
  semaphore in `tools/run_cbmc.py` now bounds machine-wide load instead of the fixed 32 cap)
- **Evidence:** same entry as above.
- **Commit:** a0620ca

## Give the inner agent a richer per-function prompt

- **Hypothesis:** The harness prompt is just `Verify <function> in <file>`. The agent then spends
  turns discovering the tool command, reading the file, and locating callees. Putting the function
  source, the callees' current contracts, the exact `avocado-run-cbmc` command (with `-I` flags),
  and a direct instruction to run it first into the prompt should cut turns (agent time and cost)
  without lowering quality.
- **Axis:** agent time
- **Status:** in progress (treatment T2, measured after T1)
- **Evidence:** WORK_SO_FAR.md entry "Treatment T2: workflow guidance and a per-function prompt".
- **Commit:** cc8f99d

## Guide callee contracts away from `__CPROVER_is_fresh` on possibly-aliasing pointers

- **Hypothesis:** The committed `swap` spec in quicksort requires `is_fresh(a)` and `is_fresh(b)`;
  when `partition` calls `swap(&arr[i], &arr[j])` the replaced contract's precondition fails, so
  `partition` (and everything above it) cannot verify and scores nothing. Telling the agent to
  check in-file callers before choosing `is_fresh` for pointer parameters (prefer
  `__CPROVER_r_ok`/`__CPROVER_w_ok` when callers pass pointers into one object) should raise the
  number of verified callers and thus aggregate kill score.
- **Axis:** quality
- **Status:** open
- **Evidence:** `avocado-run-cbmc --function partition --file eval/benchmarks/quicksort/quicksort.c`
  fails on `swap.precondition.*` at the committed specs.
- **Status update:** folded into treatment T2 (a "Writing contracts" section in `CLAUDE.md`).
- **Commit:** cc8f99d

## Tell the agent what kind of postcondition kills mutants

- **Hypothesis:** Mutants are operator swaps in the body; they survive when postconditions only
  bound results. Prompting for exact functional postconditions (return value / outputs equal to an
  expression over `__CPROVER_old` inputs, loop results via `__CPROVER_forall`) should raise kill
  scores. The committed specs score 0.0 on most csv_parser and mkey functions, which suggests the
  current prompt does not push hard enough in this direction.
- **Axis:** quality
- **Status:** open
- **Evidence:** `avocado-experimental-data/baseline-{csv_parser,mkey}.jsonl`.
- **Status update:** folded into treatment T2 (a "Writing contracts" section in `CLAUDE.md`).
- **Commit:** cc8f99d

## Shorter per-mutant CBMC budget for the agent-facing tool

- **Hypothesis:** `avocado-run-cbmc` verifies every mutant under the full 600 s timeout. In the
  first baseline agent run, 13 of `partition`'s 14 mutants were decided within 95 s while one ran
  the full 600 s and was reported as timed out (undecided) anyway; the agent's Bash call then hit
  Claude Code's own 600 s limit, was backgrounded, and the agent spent several extra turns probing
  CBMC by hand. Giving mutants a 120 s budget in the agent-facing tool only (the evaluation metric
  keeps 600 s, and a timed-out mutant is never counted as killed) should cut agent wall-clock and
  cost on such functions with no effect on the score of the specs it produces.
- **Axis:** agent time
- **Status:** in progress
- **Evidence:** transcript of the baseline run-1 `partition` session (session
  c58d4aa0-24c6-435d-abf1-78dea9e6cf7f); per-mutant completion times in
  `avocado-experimental-data/runs/baseline/1/quicksort/quicksort__mutant_*-cbmc-runs.jsonl`.
- **Commit:** a0620ca

## Cache mutant verdicts inside one agent session

- **Hypothesis:** Each `avocado-run-cbmc` call re-verifies every mutant, even when the spec has not
  changed since the last call. Keying results on a hash of the mutant source (which embeds the spec)
  would make repeated identical calls free. Likely low payoff: the agent normally changes the spec
  between calls, and killed/survived is not monotone in spec strength, so nothing can be reused
  across spec edits.
- **Axis:** harness time
- **Status:** open
- **Evidence:**
- **Commit:**

## Guarded postconditions kill nothing; prefer total ones over a narrower precondition

- **Hypothesis:** In the T2 runs the agent wrote `parse_csv` postconditions that are all
  implications with very specific antecedents (`line[0] == '"' && line[3] == '\0' && ...`). Every
  such clause is vacuously true on almost every input the precondition allows, so the spec killed
  0 of 10 decided mutants despite being long and detailed. Telling the agent to prefer a clause
  that constrains the result for *every* input the precondition allows -- narrowing the
  precondition when that is what it takes -- should raise kill scores on the parser-shaped
  functions where the current guidance produces case-split specs.
- **Axis:** quality
- **Status:** open
- **Evidence:** `avocado-experimental-data/t2-2-csv_parser.jsonl` (`parse_csv` 0/10 decided);
  the spec is in `avocado-experimental-data/runs/t2/2/csv_parser/csv.c`.
- **Commit:**

## Re-run a session when the function verifies but mutants survive

- **Hypothesis:** `avocado_verify.is_spec_improvable_with_mutation_testing` returns False as soon
  as the function verifies, so the harness never spends a second session on a spec that verifies
  with a kill score of 0. Gating the re-run on the kill score instead (re-run while mutants
  survive and the previous session raised the score) would spend agent time exactly where quality
  is lowest. Cost: strictly more agent time, so it is only worth keeping if the kill-score gain is
  larger than the run-to-run spread.
- **Axis:** quality (at the cost of agent time)
- **Status:** open
- **Evidence:** `avocado_verify.py:436`; several T2 functions verified at kill score 0.0 and
  received exactly one session.
- **Commit:**

## Always inject CBMC's C-library models

- **Hypothesis:** `tools/run_cbmc.py` runs `goto-instrument --add-library` only on the
  macro-suppression retry. Without it, unstubbed libc calls (`strlen`, `strncpy`, `memcpy`,
  `malloc`) are nondeterministic, so a caller's result is unconstrained and no postcondition can
  distinguish a mutated body: `parse_csv` (0/10) and `fread_csv_line` (0/23) score zero in *both*
  arms of the agent experiment while the libc-free quicksort functions score 1.0. Injecting the
  models on every run should make those functions' behaviour observable and their mutants
  killable. Risk: modelled libc is stricter than nondeterministic libc, so specs that verified
  before may stop verifying, which would lower the score instead.
- **Axis:** quality
- **Status:** refuted
- **Evidence:** Adding `goto-instrument --add-library` to every pipeline run (worktree
  `/root/avocado-addlib`, branch `addlib`, measured with
  `scripts/experiments/measure_quality.sh addlib <benchmark>`) raised no function's kill score and
  cost four functions their verification: `fread_csv_line` (csv_parser) and `mkey_read_aes_key`,
  `mkey_read_hmac_key`, `mkey_read_mkey_file` (mkey) went from "verified, kill score 0.0000" to
  "did not verify". Every other function scored identically. The risk in the hypothesis is what
  happened: modelled libc is stricter, and the specs written against nondeterministic libc do not
  survive it. Compare `avocado-experimental-data/addlib-*.jsonl` with `baseline-*.jsonl`.
- **Commit:** not merged; the branch is kept for reference.
