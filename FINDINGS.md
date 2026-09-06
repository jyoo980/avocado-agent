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
- **Status:** open
- **Evidence:** kilo's committed-spec evaluation is dominated by a handful of multi-minute CBMC
  runs that execute back-to-back (see baseline `.time` logs).
- **Commit:**

## Raise the mutant worker cap

- **Hypothesis:** `_MAX_MUTATION_WORKERS = 32` caps concurrency below the machine's 128 cores; a
  function with >32 mutants (e.g. `hexdump`: 42, `mkey_generate_v3_v4`: 33) waits on a second wave.
  Bounding by `os.cpu_count()` alone is a free speedup on big machines and a no-op elsewhere.
- **Axis:** harness time
- **Status:** open
- **Evidence:**
- **Commit:**

## Give the inner agent a richer per-function prompt

- **Hypothesis:** The harness prompt is just `Verify <function> in <file>`. The agent then spends
  turns discovering the tool command, reading the file, and locating callees. Putting the function
  source, the callees' current contracts, the exact `avocado-run-cbmc` command (with `-I` flags),
  and a direct instruction to run it first into the prompt should cut turns (agent time and cost)
  without lowering quality.
- **Axis:** agent time
- **Status:** open
- **Evidence:**
- **Commit:**

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
- **Commit:**

## Tell the agent what kind of postcondition kills mutants

- **Hypothesis:** Mutants are operator swaps in the body; they survive when postconditions only
  bound results. Prompting for exact functional postconditions (return value / outputs equal to an
  expression over `__CPROVER_old` inputs, loop results via `__CPROVER_forall`) should raise kill
  scores. The committed specs score 0.0 on most csv_parser and mkey functions, which suggests the
  current prompt does not push hard enough in this direction.
- **Axis:** quality
- **Status:** open
- **Evidence:** `avocado-experimental-data/baseline-{csv_parser,mkey}.jsonl`.
- **Commit:**

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
