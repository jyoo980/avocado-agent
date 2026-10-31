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
The main documentation can be found at https://diffblue.github.io/cbmc/index.html,
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

Do not read the harness's Python sources, and do not experiment with CBMC's `--depth` or
`--unwind` bounds: the bounds are fixed by the harness and are not yours to tune. Spend your turns
on the contract itself.

## Vacuous proofs

A contract is checked by assuming its preconditions, running the body and asserting its
postconditions where the function returns. If no execution gets that far, nothing is asserted and
CBMC reports no failure whatever the postconditions say. Such a proof is *vacuous*: it proves
nothing, every mutant survives it, and it does not count as verified.

`avocado-run-cbmc` checks each passing run with one more CBMC run, on a copy of your file to which
it adds a false postcondition and a false assertion at the top of the body. It reports `VACUOUS`
with one of two causes:

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

## Rules

- Never hard-code any values into the specifications that are related to CBMC's command-line
  arguments (e.g., the `N` in `--partial-loops --unwind <N>`).
- Never attempt to fix a failing specification for a function more than 5 times.
- Do not attempt to verify `main` functions.
- Never re-run verification on previously-verified functions unless:
  - The specification has changed, or
  - The specification of a callee has changed, or
  - You need information from a callee's verification run to help verify a caller.
- You must not delete any scripts if you create and use them to help you.
- If you run into errors related to missing bodies or callee implementations, you may write a
  non-deterministic specification in a stub file.
