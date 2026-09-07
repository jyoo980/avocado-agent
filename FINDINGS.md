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
- **Status:** confirmed
- **Evidence:** Measured as part of treatment T2 over three paired runs against the baseline
  (WORK_SO_FAR.md, "Agent measurement"). Mean agent time fell from 2070.9 s to 580.8 s with the
  kill score unchanged at 0.500 in every run. The prompt is not the whole of that win -- the
  `CLAUDE.md` workflow section is in the same treatment -- but the prompt is what removes the
  turns the baseline spent locating the file, its callees and the tool command.
- **Commit:** cc8f99d

## Guide callee contracts away from `__CPROVER_is_fresh` on possibly-aliasing pointers

- **Hypothesis:** The committed `swap` spec in quicksort requires `is_fresh(a)` and `is_fresh(b)`;
  when `partition` calls `swap(&arr[i], &arr[j])` the replaced contract's precondition fails, so
  `partition` (and everything above it) cannot verify and scores nothing. Telling the agent to
  check in-file callers before choosing `is_fresh` for pointer parameters (prefer
  `__CPROVER_r_ok`/`__CPROVER_w_ok` when callers pass pointers into one object) should raise the
  number of verified callers and thus aggregate kill score.
- **Axis:** quality
- **Status:** confirmed
- **Evidence:** `avocado-run-cbmc --function partition --file eval/benchmarks/quicksort/quicksort.c`
  fails on `swap.precondition.*` at the committed specs.
- **Status update:** folded into treatment T2 (a "Writing contracts" section in `CLAUDE.md`).
  With it, every agent run of both arms verified `partition` and `quickSort` and scored 1.0000 on
  quicksort (21/21 mutants), against the committed specifications' 0.7143 with `partition`
  unverifiable. The baseline arm reaches 1.0000 too, so this is not attributable to the guidance
  alone; what is attributable is that no run of either arm reproduced the committed specs' failure.
- **Commit:** cc8f99d

## Tell the agent what kind of postcondition kills mutants

- **Hypothesis:** Mutants are operator swaps in the body; they survive when postconditions only
  bound results. Prompting for exact functional postconditions (return value / outputs equal to an
  expression over `__CPROVER_old` inputs, loop results via `__CPROVER_forall`) should raise kill
  scores. The committed specs score 0.0 on most csv_parser and mkey functions, which suggests the
  current prompt does not push hard enough in this direction.
- **Axis:** quality
- **Status:** noise on the iteration tier
- **Evidence:** `avocado-experimental-data/baseline-{csv_parser,mkey}.jsonl`.
- **Status update:** folded into treatment T2 (a "Writing contracts" section in `CLAUDE.md`) and
  sharpened further in T3. Neither moved the iteration tier's kill score: all twelve completed
  agent runs killed exactly 22 of 55 decided mutants. The tier cannot show a difference here --
  see "csv_parser's libc-heavy functions are the quality ceiling" -- so this hypothesis is
  untested rather than wrong, and would need a benchmark with headroom to decide.
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
- **Status:** confirmed
- **Evidence:** Kept as part of the change measured in WORK_SO_FAR.md, "Agent measurement": no
  treatment session was killed by the harness timeout in any of the three paired runs, against a
  mean of one per baseline run. The budget is not separable from the `CLAUDE.md` guidance in that
  measurement; both target the same failure. Original observation: transcript of the baseline
  run-1 `partition` session (session
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
- **Status:** open, not pursued
- **Evidence:** Deciding it needs a hash of the specification at each `avocado-run-cbmc` call, which
  the harness does not record, so it cannot be settled from the logs already collected. The prior
  is poor: the agent edits the contract between calls in nearly every transcript read, and a cache
  keyed on the mutant source (which embeds the contract) would then never hit. It is recorded here
  so it is not re-investigated without first adding that instrumentation.
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
- **Status:** refuted (no effect; kept in history as commit `fcba9af`, reverted by `00c1264`)
- **Evidence:** Treatment T3 added exactly this paragraph to `CLAUDE.md` and was measured over
  three paired runs against the treatment without it (`compare_arms.py --baseline final
  --treatment t3 --runs 7 8 9 --benchmarks quicksort csv_parser`). Every run of both arms produced
  the identical score: mean kill score 0.5000, pooled 0.4000, 22/55 decided mutants killed, 8/8
  functions verified. Agent-time deltas were -41 s, +7 s, +19 s -- noise. The functions that score
  zero (`parse_csv` 0/10, `fread_csv_line` 0/23) do so for a structural reason, not because the
  agent chose guarded postconditions: see "csv_parser's libc-heavy functions are the quality
  ceiling".
- **Commit:** `fcba9af`, reverted by `00c1264`

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

## csv_parser's libc-heavy functions are the quality ceiling

- **Hypothesis:** `parse_csv` and `fread_csv_line` score 0 in every arm and every run measured so
  far, while the libc-free quicksort functions score 1.0 in every run. Their bodies are driven by
  unstubbed external calls (`malloc`, `strdup`, `free`, and a `getc` macro), which CBMC treats as
  nondeterministic, so no postcondition over the returned buffer can distinguish a mutated body.
  Until those calls are modelled or stubbed, no prompt change can raise csv_parser's kill score,
  and the iteration tier's kill score is therefore insensitive to prompt work.
- **Axis:** quality
- **Status:** confirmed (as a diagnosis; no fix found -- the obvious one is refuted above)
- **Evidence:** identical 22/55 pooled kills in all 12 agent runs across four arms
  (`avocado-experimental-data/{base,final,t3}-*-{quicksort,csv_parser}.jsonl`); the one attempted
  fix, always injecting CBMC's library models, is refuted in its own entry.
- **Commit:**

## The inner agent edits the benchmark body, not just its contract

- **Hypothesis:** `CLAUDE.md` tells the agent not to change the C code, but nothing enforces it.
  In the run-7 `fread_csv_line` spec the agent added a static helper function (legitimate: CBMC
  allows deterministic helpers in clauses) *and* a dozen `__CPROVER_assert` statements inside the
  function body. In-body assertions are checked by CBMC and can kill mutants that the contract
  alone would not, so the kill score stops being a measure of the contract's strength. A harness
  check that the function body is byte-identical to the original would keep the metric honest.
  Counter-consideration: such a check can only lower measured scores, and the tie-break rule
  treats quality as a hard floor, so it must be introduced as a metric change with its own
  re-measurement rather than as an improvement.
- **Axis:** quality (metric validity)
- **Status:** open
- **Evidence:** `avocado-experimental-data/runs/final/7/csv_parser/fread_csv_line.c` lines 125-176.
- **Commit:**

## The harness spends up to 90 minutes on a session that never runs the verifier

- **Hypothesis:** `avocado_verify` gives a function up to `_MAX_AGENT_SESSIONS_PER_FUNCTION` (3)
  sessions of `_DEFAULT_CLAUDE_TIMEOUT_SEC` (1800 s) each while the agent has made fewer than two
  verification attempts. A session that never calls `avocado-run-cbmc` at all therefore costs the
  full 30 minutes and is retried twice, for 90 minutes on one function, with nothing to show. Two
  candidate fixes: (a) tell the retry prompt that the previous session ended without running the
  verifier, so the next one starts differently; (b) give the *first* retry a shorter timeout, on
  the grounds that a session which has produced no attempt in 30 minutes is not about to.
- **Axis:** agent time
- **Status:** open (partially addressed by the `CLAUDE.md` workflow section, which removed every
  timeout in three paired treatment runs; the structural fix is untested)
- **Evidence:** baseline run 4 spent 5679 s of agent time against the treatment's 1151 s, almost
  all of it in three killed sessions; see the "Agent measurement" entry in `WORK_SO_FAR.md`.
- **Commit:**

## Usage limits, not machine time, bound how much agent measurement is possible

- **Hypothesis:** n/a -- this is an operational constraint worth recording so it is not
  rediscovered. Nine concurrent agent runs exhausted the account's usage limit in about 75 minutes,
  truncating all nine mid-benchmark. Two concurrent runs over the eight-function iteration tier
  complete comfortably, but two concurrent runs over mkey (47 functions) exhausted it again after
  roughly eight functions. Agent-time experiments must therefore be planned around a budget of
  roughly one iteration-tier pair per hour, and a confirmation-tier pair needs a fresh window.
- **Axis:** agent time (measurement capacity)
- **Status:** confirmed
- **Evidence:** `avocado-experimental-data/{base,final}-12-mkey.jsonl` and the `USAGE_LIMITED`
  outcomes in `avocado-experimental-data/runs/{base,final}/12/`; the abandoned runs 1-3 of labels
  `baseline`, `t1` and `t2`.
- **Commit:**

## Let the agent raise `--depth` monotonically

- **Hypothesis:** `DEVELOPMENT.md` proposes letting agents raise (never lower) CBMC's `--depth`,
  on the grounds that agents report mutants they cannot kill because of the default bound of 200.
  CBMC itself prints "Depth-bounded analysis may yield unsound verification results" on every run,
  so raising the bound is strictly sounder and is explicitly permitted by the goal's soundness
  invariants.
- **Axis:** quality (at a large cost in harness time)
- **Status:** refuted as a global change; open as a per-function, agent-chosen one
- **Evidence:** Raising `_CBMC_DEPTH` from 200 to 2000 for everything (branch `depth2000`,
  worktree `/root/avocado-depth`) and re-scoring the checked-in specifications:

  | Benchmark | depth 200 | depth 2000 |
  | --- | --- | --- |
  | quicksort wall-clock | 2.94 s | 28.82 s |
  | csv_parser wall-clock | 3.69 s | 600.58 s (hit the CBMC timeout) |
  | quicksort functions scored | 1 (`quickSort`, 0.7143) | 0 |
  | csv_parser functions scored | 2 | 1 |

  Not one function's kill score rose. Every function that changed changed for the worse:
  `quickSort`, `partition` and `fread_csv_line` went from verifying to "did not verify", because a
  deeper analysis checks paths their contracts do not cover. Files:
  `avocado-experimental-data/depth2000-*.jsonl`.
- **Why the per-function version is still open:** the measurement above raises the bound for
  everything at once, which is not the proposal. An agent raising it for one function would see the
  same effect -- its contract stops verifying -- and would then have to strengthen the contract
  until it verifies at the higher bound, which is exactly the work that produces a stronger
  specification. The incentive is self-correcting: a function that does not verify is not scored at
  all, so an agent cannot raise the bound and walk away.
- **Design problem to solve first:** the bound lives in `tools/run_cbmc.py` and is used both by the
  agent's tool and by `evaluate_specification_quality.py`. If the agent picks a depth per function,
  the scorer must use the same depth for that function, or a specification tuned at depth 2000 is
  graded at depth 200 and the two disagree. The chosen bound would have to become part of the
  function's recorded specification (a machine-readable annotation the harness reads back), not a
  transient command-line flag. That also keeps `CLAUDE.md`'s "never hard-code CBMC command-line
  values into specifications" rule honest: the value would live in harness metadata, not in a
  clause.
- **Commit:** not merged; branch `depth2000` kept for reference.

## Give the mutant pipeline a budget matched to observed decision times

- **Hypothesis:** every mutant gets the full 600 s CBMC budget, but mutants are decided far faster
  than that. All 42 of `hexdump`'s mutants are decided between 0.4 s and 1.4 s, a margin of more
  than 400x. Kilo's 414 s evaluation, by contrast, is dominated by a few runs that consume the
  whole 600 s and are then discarded as undecided. A budget derived from the unmutated function's
  own verification time (say a fixed multiple of it) would cut the tail without touching any
  mutant that was ever going to be decided.
- **Axis:** harness time
- **Status:** open
- **Evidence:** per-mutant `seconds`, now recorded on every `MutantVerificationResult` and emitted
  by `eval/mutants/generate_mutants_and_compute_score.py`, measured on
  `eval/benchmarks/mkey/source/utils.c#hexdump`.
- **How to keep the metric frozen:** this changes which mutants are decided, so it may not be
  merged on the strength of the idea. Run the full benchmark set at 600 s with per-mutant times
  recorded, find the slowest *decided* mutant, and set the budget above it; then re-score
  everything and require identical records. If any decided mutant is slower than the proposed
  budget, the budget is wrong, not the mutant.
- **Commit:**

## Schedule mutants longest-first

- **Hypothesis:** mutants are submitted to the pool in source order. With a bounded pool the
  makespan is minimised by starting the longest jobs first, and a mutant's cost is predictable from
  the same mutant's cost on the previous run of that function. Ordering by the previous run's
  recorded `seconds` (falling back to source order for a first run) should shorten the wall-clock
  of the slow benchmarks without touching any verdict, since scheduling cannot change a result.
- **Axis:** harness time
- **Status:** open
- **Evidence:** none yet; the per-mutant `seconds` needed to drive it are now recorded.
- **Commit:**

## Stub the specific libc functions the benchmarks call

- **Hypothesis:** the confirmed csv_parser ceiling comes from `malloc`, `strdup`, `free` and a
  `getc` macro being nondeterministic. Injecting CBMC's whole library is refuted (it broke four
  functions), but `stubs/` already supports per-symbol models keyed by a `/* FUNCTION: name */`
  marker and applied only to the callees a function actually has. Hand-written deterministic models
  for the handful of allocation and string functions these benchmarks use would make callers'
  results observable, one symbol at a time, with the blast radius of each stub measurable on its
  own.
- **Axis:** quality
- **Status:** open
- **Evidence:** "csv_parser's libc-heavy functions are the quality ceiling" and the refuted
  "Always inject CBMC's C-library models" entries above.
- **Commit:**

## Put the callee contracts, not just the callee names, in the prompt

- **Hypothesis:** the per-function prompt names the in-file callees whose contracts replace their
  bodies, but the agent still has to open the file to read them, and what those contracts promise
  is the entire basis for what the caller can assert. Inlining the callees' clause text should save
  turns and reduce contracts written against what the callee *does* rather than what it *promises*.
- **Axis:** agent time, possibly quality
- **Status:** open
- **Evidence:** transcripts consistently show a first turn spent reading the whole file.
- **Commit:**

## Summarise surviving mutants by location instead of listing every diff

- **Hypothesis:** `get_mutation_testing_results_for_client` emits a unified diff per surviving
  mutant, up to a 50 KB budget. On `editorUpdateSyntax` that is 62 diffs, and on `hexdump` 42, most
  of them one-line variations on the same expression. Grouping survivors by line and operator class
  ("all 31 survivors are the relational operators in the loop at lines 118-140") would spend the
  agent's context on the shape of the gap rather than on 62 near-identical hunks.
- **Axis:** agent time, possibly quality
- **Status:** open
- **Evidence:** `avocado-experimental-data/baseline-kilo.jsonl` (`editorUpdateSyntax`, 0 of 62
  killed).
- **Commit:**

## Put a worked high-kill-score example in `CLAUDE.md`

- **Hypothesis:** the prompt and `docs/` explain contract syntax, and the only worked example is a
  three-line `sum`. Nothing shows a complete contract that actually kills mutants on a loop over a
  buffer, which is the shape of nearly every function in the benchmark set. A single end-to-end
  example -- function, contract, the mutants it kills, and the one it does not -- is the cheapest
  remaining prompt intervention and the kind that usually moves model output most.
- **Axis:** quality
- **Status:** open
- **Evidence:** none direct; the two prompt treatments that did *not* include an example (T2's
  prose guidance and T3's) moved the iteration tier's score not at all.
- **Commit:**

## Move to CBMC's `--dfcc` contract mode

- **Hypothesis:** `DEVELOPMENT.md` records that `--dfcc` is where the project wants to go. It is
  the supported path for contract checking in current CBMC, and the `_dfcc`-suffixed predicates in
  `docs/contracts-memory-predicates.md` (for example `__CPROVER_pointer_in_range_dfcc`) are only
  available under it, so today the agent is told about predicates it cannot use. It is a rework of
  the pipeline and the prompts rather than a tweak.
- **Axis:** quality
- **Status:** open
- **Evidence:** `DEVELOPMENT.md`; `docs/contracts-memory-predicates.md`.
- **Commit:**

## Kilo's harness time is two undecidable functions, not a thousand small ones

- **Hypothesis:** the remaining harness time on the slowest benchmark is concentrated in CBMC runs
  that never return a verdict, so capping the per-run budget at a validated multiple of the slowest
  *decisive* run would cut it several-fold without changing a single score.
- **Axis:** harness time
- **Status:** open, with the supporting measurement done
- **Evidence:** kilo scored with `--keep-artifacts`, then the per-step `seconds` now recorded in
  each `<stem>-cbmc-runs.jsonl` aggregated over all 156 pipeline runs:

  | | count | time |
  | --- | ---: | ---: |
  | verified (rc 0) | 110 | median 3.1 s, max 5.6 s |
  | killed / failed (rc 10) | 33 | median 6.2 s, max 12.5 s |
  | undecided (timeout or crash) | 13 | 1054 s |

  Total CBMC subprocess time is 1576 s, of which the 13 undecided runs are 1054 s, or 67%. Two
  functions account for almost all of it: `editorDelRow` hits the 600 s timeout and
  `editorInsertRow` aborts (rc 134) after 408 s. Neither is scored either way, so that time buys
  nothing. Meanwhile *every* run that returned a verdict finished within 12.5 s -- the 600 s budget
  is roughly 48x the slowest decisive run. Because the evaluation is now parallel, the wall-clock
  floor is the single slowest run, so the 600 s timeout alone sets it.
- **How to keep the metric frozen:** shortening the budget changes which mutants are decided, so it
  may not be merged on the strength of this alone. Record the decision time of every run across all
  four tiers, take the slowest decisive one, set the cap comfortably above it, re-score everything
  and require identical records. If any decided run is slower than the proposed cap, the cap is
  wrong.
- **Commit:**

## Specify independent files concurrently

- **Hypothesis:** `avocado-verify` walks one function at a time, and the experiment driver walks one
  file at a time, so a program's specification time is the sum over every function. The call graph
  is built per file and has no cross-file edges, so different files have no ordering constraint at
  all; within a file, functions in disjoint parts of the call graph do not constrain each other
  either. Running one agent session per file concurrently would cut the wall-clock to fully specify
  a multi-file program to roughly its largest file.
- **Axis:** agent time -- by far the larger clock. On mkey the agent pass is about 3200 s against
  5 s of harness time, so agent time is over 99% of the total.
- **Status:** open
- **Evidence:** `avocado-experimental-data/final-14-mkey.time` (four files, walked in sequence);
  `tools/util/tree_sitter_utils.get_call_graph` builds the graph from a single file.
- **Risks to measure:** concurrent sessions multiply the rate at which the account's usage limit is
  consumed, which is already the binding constraint on measurement; and they contend for the
  machine during mutation testing, which the subprocess semaphore bounds but does not eliminate.
  Both are wall-clock-versus-throughput trade-offs to quantify, not correctness problems.
- **Commit:**

## Skip the pipeline re-run when CBMC has already returned a verdict

- **Hypothesis:** `run_cbmc` retries the whole pipeline when the output mentions a missing callee
  body, and that check fires even when the first `cbmc` call already returned a decisive 0 or 10.
  Benchmarks full of unstubbed libc calls should therefore verify many mutants twice for nothing.
- **Axis:** harness time
- **Status:** refuted as a free win; open as a deliberate metric change
- **Evidence:** on kilo, 101 pipelines had a decisive first verdict and were re-run anyway. The
  re-run left the verdict unchanged in 86 of them and *changed it* in 15, so the retry is doing
  real work, not just burning time. The redundant portion is 104 s of 1576 s, about 7% -- much less
  than the doubling the hypothesis assumed, because the re-runs are cheap next to the two
  undecidable functions. Skipping it would be a metric change affecting 15 verdicts on one
  benchmark, for a 7% saving; the budget cap above is the better lever.
- **Commit:**

## Correction: where the time to specify a program actually goes

- **Hypothesis:** n/a -- this corrects a framing error in earlier entries and in `WORK_SO_FAR.md`.
  The harness-time numbers reported for the parallel-evaluation change (kilo 1103 s -> 414 s, and
  so on) are for `eval/mutants/evaluate_specification_quality.py`, the *offline scoring pass* used
  to grade a benchmark after the fact. That is harness time by the goal's definition, but it is a
  measurement tool: it is not part of `avocado-verify`, and speeding it up does not by itself speed
  up specifying a program.
- **Axis:** harness time and agent time (their relative size)
- **Status:** confirmed
- **Evidence:** measured on mkey's 49 functions, using the specifications the treatment arm of run
  14 produced:

  | | time |
  | --- | ---: |
  | one full pass of `avocado-run-cbmc` over all 49 functions: verification | 41 s |
  | the same pass: mutation testing, 221 mutants | 22 s |
  | **total CBMC work for one pass over the program** | **63 s** |
  | agent wall-clock for the same program (`claude -p`) | 3223 s |

  So all CBMC work, mutants included, is about 2% of the wall-clock to specify mkey; even at three
  tool calls per function it stays under 6%. The verification runs logged during the actual agent
  pass corroborate it: 148 pipeline runs totalling 127 s of a 3223 s pass. Per call the split is
  small in absolute terms -- `hexdump` is 0.55 s to verify and 1.52 s to score its 42 mutants.
- **What this means for the two clocks:** on programs like mkey the loop is model-latency bound, and
  the levers are the number of turns and sessions, not CBMC. The exception is bimodal, not
  marginal: on kilo a single verification of `editorDelRow` runs 602 s to a timeout and
  `editorInsertRow` aborts after 408 s, so one pathological function costs the agent more than ten
  typical functions and can consume a whole 1800 s session. The per-run budget cap proposed above
  therefore matters in the generation loop, not only in the scoring pass.
- **Also worth being accurate about:** mutants were *already* verified in a 32-worker thread pool
  before any change here. What changed in the loop was the cap (32 to the CPU count, which only
  binds on functions with more than 32 mutants), the process-wide subprocess semaphore, the
  scratch-directory isolation, and the 120 s per-mutant budget. The large parallelism win was in
  the scoring pass, which had none.
- **Commit:**

## Cost-aware session scheduling: decide per function how much to spend

- **Hypothesis:** the harness spends the same shape of effort on every function -- one fresh
  session, a floor of two verification attempts, up to three sessions of 1800 s -- although it can
  tell in advance, for free, that functions differ by an order of magnitude in what that effort can
  buy. Two signals it already has should drive three different policies.
- **Axis:** agent time (primarily), quality (by redirecting the time saved)
- **Status:** open
- **Evidence:** mkey run 14, treatment arm, agent time split by whether the function has any
  mutants at all:

  | | functions | agent time | per function |
  | --- | ---: | ---: | ---: |
  | no mutants | 27 | 909 s (28%) | 34 s |
  | has mutants | 21 | 2238 s (69%) | 107 s |

  The 27 functions with no mutants cannot move the kill score by construction -- the scorer reports
  "no mutable operators" and excludes them -- yet they consume 28% of the wall-clock, one cold-start
  session each. Twelve of them are the same one-line byte-order accessor written twelve times
  (`getbe16`/`getbe32`/`getbe64`/`getle*`/`putbe*`/`putle*`).
- **The two free signals:** `eval.mutants.mutate_function.get_mutants` is a pure tree-sitter walk
  and is already called by `avocado_verify.is_spec_improvable_with_mutation_testing`, so the mutant
  count is known *before* a session starts; and the wall-clock of the first `avocado-run-cbmc` call
  says which CBMC regime the function is in (about 1 s on mkey, 400-600 s on kilo's
  `editorDelRow` and `editorInsertRow`).
- **Three policies this suggests:**
  1. *Zero mutants:* batch several such functions into one session instead of paying a cold start
     each. They still need good contracts, because callers verify against them under
     `--replace-call-with-contract` -- the `swap`/`partition` trap is exactly this -- so the
     measurement must be caller verification and downstream kill scores, not just wall-clock.
  2. *Has mutants, CBMC fast:* the current path, unchanged. This is where the 69% goes and where
     the kill score is actually won.
  3. *CBMC does not return within a short budget:* cap the call and tell the agent why, namely that
     its precondition has to bound the state space before any contract can be checked. That turns
     an open-ended sink into a specific instruction, and it is the only one of the three that helps
     kilo-shaped functions.
- **Caveat on scope:** the split above is measured on mkey. No agent pass was ever run on kilo, so
  the claim that kilo's *loop* is CBMC-bound is inferred from its CBMC timings (a single 602 s
  verification), not measured end to end. That measurement should come first.
- **Commit:**

## Record the kill score during generation instead of re-deriving it afterwards

- **Hypothesis:** `avocado-run-cbmc` already computes a kill score on every successful call, but the
  harness throws it away and a separate offline pass recomputes it later. Persisting the last score
  per function in the run log would make the scoring pass unnecessary for routine use.
- **Axis:** harness time
- **Status:** open
- **Evidence:** `tools/run_cbmc_and_mutation_testing.main` prints the score and discards it;
  `avocado_verify` records only the ground-truth pass/fail.
- **Design detail that must not be missed:** the agent-facing tool now verifies mutants with a 120 s
  budget while the frozen metric uses 600 s (this divergence was introduced deliberately, to bound
  how long a session waits for feedback). A score recorded during generation is therefore not
  necessarily the metric's score. Either record the budget alongside the score, or have the harness
  re-run the final scoring at the metric's budget once per function when the function is finished --
  which is far cheaper than a whole second pass, since it is one function rather than the program.
- **Commit:**

## Filter equivalent mutants, or let the agent nominate them

- **Hypothesis:** `avocado-run-cbmc` runs CBMC on every mutant without asking whether some are
  equivalent to the original. Detecting equivalents up front, or letting the agent nominate
  likely-equivalent mutants so CBMC skips them (recording what was skipped), would cut mutation
  time and stop the agent chasing unkillable mutants.
- **Axis:** harness time and agent time
- **Status:** open, with two of its three forms ruled out on the evidence below
- **Evidence and assessment:**
  - *Where the time is.* Mutation testing is not where the loop's time goes on the programs
    measured: all 221 mkey mutants take 22 s in a 3223 s pass, and kilo's decisive mutants finish
    within 12.5 s. Filtering every equivalent mutant would recover under 1% here. That may not hold
    for a large file, where each mutant recompiles the whole file (goto-cc was 216 s of kilo's
    1576 s of CBMC time, about 1.4 s per compile of 1564 lines) -- but the saving is then in
    compiling, and a filter only helps if equivalents are a large fraction of mutants.
  - *Are survivors equivalent?* A spot check on mkey says no. `reverse_words` survivors:
    `i < words` mutated to `i > words` (the loop never runs) and `size % sizeof(u32)` mutated to
    `size * sizeof(u32)` (returns early on any non-zero size); `align64` survivors flip the sign
    of the rounding term. None is equivalent. They survive because the contract has no `ensures`
    about the buffer's contents, i.e. the specification is weak, which is the signal the score is
    supposed to send. Removing them would hide it.
  - *Form 1, filter at generation:* permitted by the goal only as a documented, tested rule with
    equivalents reported separately, and any change to the mutant set has to be re-validated
    against the frozen scorer on every tier. Syntactic equivalence rules (e.g. `<` vs `!=` on a
    loop counter that only increments) are rare in this operator set and error-prone under
    `--partial-loops --unwind 5`, where a mutant can be equivalent only *within the bound*.
  - *Form 2, the agent decides:* unsound as stated. A mutant the agent declares equivalent and CBMC
    never runs cannot count as killed, and dropping it from the denominator on the agent's word is
    exactly the gaming the goal forbids. Counting it as survived removes the incentive to nominate.
  - *The sound form worth building:* let the **harness** prove equivalence, and use the proof as
    *feedback*, not as a filter. For each survivor, one differential CBMC run -- original and
    mutant called on the same nondeterministic inputs under the precondition, assert the observable
    results agree -- labels it "provably equivalent within the bounds" or "survived: the contract
    does not distinguish it". The mutant stays in the denominator either way; what changes is that
    the agent stops spending turns on the first kind. The cost is one extra cheap CBMC run per
    survivor. The hard part is the assertion for functions that write through pointers, which needs
    the same `assigns` machinery the contract already declares.
- **Commit:**

## Generic ways to shorten the generation loop (not benchmark-specific)

- **Hypothesis:** the loop's cost on every program measured is model turns, not CBMC. The changes
  that shorten it without depending on how these particular benchmarks look are the ones that
  remove turns from *every* session.
- **Axis:** agent time
- **Status:** open
- **Candidates, each with its own measurement:**
  1. *Have the agent emit the contract; have the harness insert it.* Today the agent reads the
     file, edits it, and re-reads to confirm -- three turns before the first verification. The
     clause stripper already locates every function's span, so the harness can splice a returned
     contract in itself. Side benefit: the agent structurally cannot edit the body, which closes
     the "in-body `__CPROVER_assert`" metric-validity finding above.
  2. *Give the agent a verification slice, not the file.* The function, its callees' contracts, and
     its callers' call sites are what a contract depends on. For a 1564-line `kilo.c` the whole
     file is read every session; for a file the size of `lz4.c` it is the dominant token cost. This
     is the change the current benchmarks undersell most, and the one most likely to matter on the
     validation tier.
  3. *Carry context across the functions of one file.* Every function is a cold `claude -p`
     session that rediscovers the file's conventions. Either resume the previous session or pass a
     short per-file summary (what verified, which predicates worked) forward. Measure turns per
     function.
  4. *Import the reference docs into the cached prompt.* Sessions spend a tool call reading
     `docs/*.md`; `CLAUDE.md` can import them so they are part of the cached system prompt instead.
     Measure tool calls per session.
  5. *Spend less model on trivial functions.* `.claude/settings.json` pins one model at effort
     `high` for every function. Twenty-seven of mkey's 49 functions have no mutants, and twelve are
     the same one-liner; a lower effort level or a cheaper model for functions below a size and
     mutant-count threshold is a config change, measurable in three paired runs, with the kill
     score as the guard.
- **Commit:**
