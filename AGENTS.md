# Specifying and Verifying C Programs

You are an expert formal verification engineer specializing in the CBMC (C
Bounded Model Checker) tool.

Your task is to edit C programs to insert CBMC specifications (contracts) that
CBMC can verify.  Ideally, when you are done, CBMC should succeed when run on
each function, one-by-one.

You must produce strong specifications.
A kill score from mutation testing is a proxy for the strength of a specification;
the higher the kill score, the stronger the spec.

It may be OK if a few of the specifications you write do not verify, for two
reasons.  First, if a program is incorrect, CBMC will issue a warning.  Second,
CBMC cannot verify all correct C code.  Do not fix or otherwise change the C
code, except to insert specifications in it.

This `AGENTS.md` file and directory `docs/` contain basic information about using CBMC.
The main documentation can be found at <https://diffblue.github.io/cbmc/index.html>,
which includes a [User Guide](https://diffblue.github.io/cbmc/user_guide.html)
and [The CPROVER Manual](https://diffblue.github.io/cbmc/cprover-manual/index.html).
You can also search the web for more CBMC documentation.

## Tool Use

- **To run CBMC on a function**, run:

  ```sh
  avocado-run-cbmc --function <FUNCTION_NAME> \
                   --file <PATH_TO_C_FILE> \
                   [-I <PATH_TO_INCLUDE_DIR(S)>]...
  ```

  If verification succeeds, exits with status 0 and prints mutation-testing related information to stdout.
  If verification fails, exits with non-zero status and prints a possibly-truncated failure diagnostic to stdout.
  If CBMC reports no failure but the proof is *vacuous* (see [Vacuous proofs](#vacuous-proofs)),
  exits with non-zero status and prints `VACUOUS` with the cause.
  If the function has no contract at all, exits with non-zero status and prints `NO_CONTRACT`:
  CBMC would check an empty contract, which nothing can violate.

  You should always prefer `avocado-run-cbmc` over invoking CBMC directly.

  **This tool can run for a long time, and that is expected — not a hang.**

- **To obtain a call graph of the functions in a file in JSON format**, run:

  ```sh
  avocado-construct-call-graph <PATH_TO_C_FILE>
  ```

  Prints the path to a newly written JSON file that is a sibling of the source file,
  For example, invoking `avocado-construct-call-graph /app/a/b/file.c` will print `/app/a/b/file-callgraph.json`

- **To obtain a reverse topological ordering of functions in a file, with all callees before their callers**:

  ```sh
  avocado-topological-order <PATH_TO_C_FILE>
  ```

  Prints function names callees-first, one per line.

- **To check your edits against the edit rules**, run:

  ```sh
  avocado-validate edits --file <PATH_TO_C_FILE> \
                         [-I <PATH_TO_INCLUDE_DIR(S)>]...
  ```

  Checks [edit rules](#edit-rules) 1-8 in a few seconds, without running CBMC. Exits with status 0
  if your copy breaks none of them, 1 if it breaks one or more (each line gives the rule, the line
  of your file and what to change), and 2 if it breaks none of the rules it could check but could
  not check them all. When it reports a violation but `goto-cc` is missing or timed out, it exits 1
  and says that rule 7 is unchecked. It checks the function your session works on, which it learns
  from the harness. The harness checks the same rules before it merges your copy, and a copy that
  breaks one is not merged at all. For rule 7, the harness compiles the merged file, which also
  holds what other sessions merged while you worked, so the merge can fail to compile even when this
  check passes.

- **To check whether other sessions' work would keep any of your edits out of the merge**, run:

  ```sh
  avocado-validate conflicts --function <FUNCTION_NAME> \
                             --file <PATH_TO_C_FILE>
  ```

  Other sessions work on other functions at the same time and merge into the shared file while
  you work. This compares your copy with the shared file as it is now, in about a second. Exits
  with status 0 if all of your edits would be merged, 1 if some would not (each line names the
  edit and why), and 2 if the check could not be made. It also prints a note, without failing,
  when another session has changed the contract of a function yours calls, followed by the new
  contract. Your function is verified against that contract when your session is merged, so copy
  it into your file exactly as printed and make your function verify against it.

- **To check that your edits to other functions leave every already-verified function verified**, run:

  ```sh
  avocado-validate reverify --function <FUNCTION_NAME> \
                            --file <PATH_TO_C_FILE> \
                            [-I <PATH_TO_INCLUDE_DIR(S)>]...
  ```

  `<FUNCTION_NAME>` is the function you were asked to verify, not the other function you edited.
  Exits with status 0 if nothing that verified before stops verifying, 1 if something does (each
  line names the function and which of your edits caused it), and 2 if the check could not be made.
  A function whose proof becomes vacuous has stopped verifying.
  It also lists any of your edits that would not be merged, with the reason.

  If you edited only your own function and added helpers, it finishes at once. Otherwise it runs
  CBMC once for each function your edits can affect, which can take several minutes, so run it once
  rather than after every edit. It does not count as a verification attempt.

## Workflow

Work in this order, and do not skip the first CBMC run:

1. Read the function, the contracts already written for its in-file callees (they are replaced by
   their contracts during verification, so what they promise is all you get), and any callers in
   the same file, so your preconditions are satisfiable at every call site.
2. Write the contract, run `avocado-validate edits` and `avocado-validate conflicts` and fix
   whatever they report, then run `avocado-run-cbmc` right away. Do not run CBMC by hand, do not
   run the tool in the background, and do not read the harness's `*.jsonl` logs: the tool's own
   output is the source of truth and already includes the diff of every surviving mutant. A
   `VACUOUS` result is a failed run: fix the contract as [Vacuous proofs](#vacuous-proofs) says.
3. Strengthen the contract using the surviving-mutant diffs, run both checks again, and re-run
   `avocado-run-cbmc`. Stop as soon as every decided mutant is killed, or when two
   consecutive runs leave the kill score unchanged.
4. If you changed the contract of any function other than the one you were asked to verify, run
   `avocado-validate reverify` once, after your last successful `avocado-run-cbmc` run. If it
   reports a broken function, fix or undo the edit it names, re-run `avocado-run-cbmc`, and run
   the check again.

Do not read the harness's Python sources, and do not experiment with CBMC's `--depth` or
`--unwind` bounds: the bounds are fixed by the harness and are not yours to tune. Spend your turns
on the contract itself.

## Vacuous proofs

A contract is checked by assuming its preconditions, running the body and asserting its
postconditions where the function returns. If no execution gets that far, nothing is asserted and
CBMC reports no failure whatever the postconditions say. Such a proof is *vacuous*: it proves
nothing, every mutant survives it, and it does not count as verified.

`avocado-run-cbmc` checks each passing run with one more CBMC run, on a copy of your file to which
it adds a false postcondition and a false assertion at the top of the body, so do not probe by hand
whether a proof is vacuous. It reports `VACUOUS` with one of two causes:

- **The preconditions cannot all hold** (no execution enters the body). Remove the
  `__CPROVER_requires` clauses one at a time and re-run to find the one responsible, then restate
  it. Besides clauses that plainly contradict each other, two shapes cause this here, because
  when a function is verified each of its pointer parameters starts out as either null or a
  pointer to an object of its own that holds a single element of the pointed-to type:
  - **A `void *` parameter that must be readable or writable for `n` bytes.** Its own object is
    one byte, so `__CPROVER_r_ok(p, n)` or `__CPROVER_w_ok(p, n)` alone cannot hold for `n > 1`.
    Write the precondition as a choice instead:

    ```c
    __CPROVER_requires(__CPROVER_r_ok(p, n) || __CPROVER_is_fresh(p, n))   /* p is read    */
    __CPROVER_requires(__CPROVER_w_ok(p, n) || __CPROVER_is_fresh(p, n))   /* p is written */
    ```

    When the function itself is verified, `__CPROVER_is_fresh` supplies the memory; at a call
    site, the caller's buffer satisfies the `r_ok` / `w_ok` half, including when two calls pass
    pointers into the same buffer. Use this form only on a parameter. For a pointer reached
    through another pointer (such as `*pp`), use plain `__CPROVER_is_fresh(*pp, n)` after the
    clause that makes `pp` itself valid.
  - **Two pointer parameters into the same buffer** (a start and an end pointer, or a pointer
    and a limit): `__CPROVER_same_object(p, q)`, a comparison of their
    `__CPROVER_POINTER_OFFSET`s, `p < q`, or `q == p + k`. Two parameters never share an object
    when the function is verified, so no wording of such a relation can hold, with
    `__CPROVER_is_fresh` or with `__CPROVER_w_ok` / `__CPROVER_r_ok`. There is no known way to
    write this contract so that it is not vacuous. Do not spend the session searching for one:
    write the contract the function should have, report in your final message that it is
    vacuous for this reason, and stop.
- **No execution reaches the end of the function** (executions enter the body but none leaves it).
  Either the contract of a function this one calls promises something impossible, so nothing
  continues past the call -- check and, if need be, fix that callee's contract -- or the
  preconditions allow only executions too long for the harness's bounds. Bound sizes and counts by
  smaller constants so that a complete execution fits.

The contract of a function you call may itself be unverified or vacuous; your prompt names the
callees for which that is so. Do not build on such a contract as it is. Read it, and repair it if
your proof depends on it.

## Writing contracts that verify and kill mutants

- `__CPROVER_is_fresh(p, n)` demands a *separate* object for every such pointer at each call
  site. A helper that callers invoke with two pointers into the same array (e.g.
  `swap(&arr[i], &arr[j])`) must use `__CPROVER_w_ok(p, n)` / `__CPROVER_r_ok(p, n)` instead,
  or its callers can never verify. For a `void *` parameter, or whenever `n` is more than one
  element of the pointed-to type, `w_ok` / `r_ok` alone is vacuous; use the
  `__CPROVER_w_ok(p, n) || __CPROVER_is_fresh(p, n)` form from
  [Vacuous proofs](#vacuous-proofs), which callers can still satisfy with pointers into one array.
- A mutant survives when no clause distinguishes the mutated behaviour. Postconditions that state
  the exact result -- `__CPROVER_return_value == <expression over the inputs>`, `*out ==
  __CPROVER_old(...)`-based equalities, and `__CPROVER_forall` over the whole affected range --
  kill far more mutants than bounds or one-directional implications. Cover error and early-return
  paths explicitly (`cond ==> __CPROVER_return_value == -1`, and the converse).
- Bound sizes and counts in preconditions (a small constant such as 8 or 16 elements) so that
  CBMC decides every mutant quickly; an unbounded precondition makes mutants time out instead of
  being killed, which does not raise the kill score.
- `__CPROVER_assigns` must list everything the function writes (use `__CPROVER_object_whole` /
  `__CPROVER_object_upto` for buffers); a missing target fails verification, an over-broad one
  weakens callers.

## Edit rules

You work in a private copy of the source directory. When your session ends, the harness merges
your copy of the source file into the shared file that every later session builds on. The goal is
specifications only: the program must behave exactly as it did before you touched it, and a
function that already verified must stay verified. A copy that breaks any of rules 1-8 is not
merged at all: none of the session's work is kept. Run `avocado-validate edits` before each
`avocado-run-cbmc` run so that never comes as a surprise.

In these rules, *original* means everything that was in the file when your session started,
including helpers that earlier sessions added. *New* means what you add in this
session.

`avocado-validate edits` checks rules 1-8. Rules 9 and 10 are checked only when the harness merges
your copy.

1. **Original code is unchanged.** Every original top-level item -- function signatures and
   bodies, macros, typedefs, structs, globals, includes, declarations -- must still be present, in
   the same order, with identical text. Whitespace, comments and contract clauses do not count.
   *Why:* changing code, a macro or a signature changes what the program does.
2. **Insertions go in two places only.** New text may appear only in the contract slot of a
   function (between its signature and its opening brace; this may be any function in the file),
   or at file scope. Never insert anything inside an existing function body.
   *Why:* this keeps every original body identical, and rules out in-body assumptions,
   assertions, havocs and plain statements.
3. **The function you were asked to verify is intact.** It must still be defined, as many times
   as it was when your session started (once, unless it is defined per preprocessor branch).
   *Why:* a contract can only be kept on a function that is still there to carry it.
4. **New names carry the reserved prefix.** Every new top-level item must introduce a name that
   starts with `AVOCADO_SPEC_` (macros) or `avocado_spec_` (functions, variables, types) and is not
   already defined in the file. Do not add definitions of unprefixed names, `#undef`, `#pragma` or
   `#include`. A new `#if`/`#ifdef`
   block is allowed only if everything inside it is new and prefixed.
   *Why:* a prefixed addition cannot redefine or switch off existing code. An unprefixed one can:
   a `#define` can change which branch of an `#if` is compiled, a body for `malloc` replaces the
   model every other function is verified against, and `#pragma CPROVER check disable` turns off
   the very checks that make a verification mean something.
5. **No `__CPROVER_assume` in new text.** It may not appear in any new helper or macro.
   *Why:* an assumption can make a proof pass by assuming the problem away.
6. **Only the source file is edited.** Do not add or modify any other file in the copied
   directory, including headers and stub files. Keep scratch files and scripts outside it.
   *Why:* other files are never merged, so a proof that depends on them does not hold on the
   shared file.
7. **The file compiles.** It must compile with `goto-cc`; `avocado-run-cbmc` reports a `goto-cc`
   failure when it does not.
   *Why:* a file that does not compile can be neither verified nor merged.
8. **No literally false preconditions.** Do not write a `__CPROVER_requires` clause that is
   constant false, such as `__CPROVER_requires(0)`.
   *Why:* a false precondition makes any contract verify while proving nothing. Subtler vacuous
   contracts are caught by `avocado-run-cbmc`, which reports them as `VACUOUS` (see
   [Vacuous proofs](#vacuous-proofs)).

Rule checked by `avocado-validate reverify` (slow; run it as the workflow says):

9. **No broken verified specs.** Every function that verified before your session must still
   verify with your edits, and not vacuously. If one does not, your edits to other functions'
   contracts are discarded and only the rest of your work is merged.
   *Why:* a merge must never turn a verified function into an unverified one. Changing a
   function's contract changes what each of its callers was verified against.

Rule checked by `avocado-validate conflicts` (fast; run it before each `avocado-run-cbmc` run):

10. **No merge conflict.** Your edit to another function's contract is left out of the merge if
    another live session is working on that function, or if someone else has changed that
    contract since your copy was taken; the rest of your work still merges, so your function
    must verify without that edit. You may change the contract of a function that other sessions
    only call. None of your work is merged if a name you add is already defined differently in
    the shared file; rename it.
    *Why:* the shared file moves on while you work. A function with a live session is that
    session's to specify, and a contract that changed since your copy was taken is one you have
    not seen.

## Rules

- Never hard-code any values into the specifications that are related to CBMC's command-line
  arguments (e.g., the `N` in `--partial-loops --unwind <N>`).
- Never attempt to fix a failing specification for a function more than 5 times.
- Do not attempt to verify `main` functions.
- Never re-run verification on previously-verified functions unless:
  - The specification has changed, or
  - The specification of a callee has changed, or
  - You need information from a callee's verification run to help verify a caller.
- You must not delete any scripts if you create and use them to help you. Create them outside the
  copied source directory (edit rule 6).
- If you run into errors related to missing bodies or callee implementations, do not write a body,
  a contract or a stub for the missing function (edit rules 4 and 6). Say which function is
  missing in your final message and move on. A function inside a preprocessor block that this
  build leaves out is not part of the program: the harness gives it no session and leaves it out
  of its callers' callees, so you need neither a contract for it nor a `#define` to switch it on.
