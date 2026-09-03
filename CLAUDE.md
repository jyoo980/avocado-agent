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

This `CLAUDE.md` file and directory `docs/` contain basic information about using CBMC.
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

  You should always prefer `avocado-run-cbmc` over invoking CBMC directly.

  **This tool can run for a long time, and that is expected — not a hang.**

  Mutant verdicts are remembered between runs. Re-running the tool without having changed the
  specification will reuse them and finish quickly; changing the specification (or a callee's
  specification) re-verifies whatever the change could have affected.

- **To mark a surviving mutant as impossible to kill**, run:

  ```sh
  avocado-mark-equivalent --function <FUNCTION_NAME> \
                          --file <PATH_TO_C_FILE> \
                          --mutant <MUTANT_ID> \
                          --reason "<why no specification can kill it>"
  ```

  Each surviving mutant in `avocado-run-cbmc` output is listed with an id, e.g.
  `--- surviving mutant 2 [id 9f3a1c...] — foo.c:42 (RELATIONAL: < -> <=) ---`.
  Use that id here.

- **To re-check the kill score without re-running full verification**, run:

  ```sh
  avocado-get-mutation-score --function <FUNCTION_NAME> --file <PATH_TO_C_FILE>
  ```

## Mutation Testing and the Kill Score

A mutant is "killed" when CBMC fails to verify the mutated program against your specification,
which shows the specification is strong enough to detect that perturbation.

Some mutants are *equivalent*: the operator swap produces a program that behaves identically to
the original, so no specification can ever kill them. Chasing these wastes effort and cannot raise
the score.

- If a mutant survives across several different specifications you have tried, or you can argue it
  is semantically equivalent to the original, record it with `avocado-mark-equivalent` and move on.
  Do not keep rewriting a specification to chase a mutant you believe is equivalent.
- Only declare a mutant equivalent when you genuinely believe no specification could kill it. The
  reason you give is recorded and may be audited. Declaring a killable mutant equivalent is a
  worse outcome than a lower kill score.
- Mutants that time out, fail to compile, or fail instrumentation are already excluded from the
  score. You do not need to do anything about them.

When any mutant has been excluded as presumed-equivalent, two scores are reported: the *adjusted*
kill score (which excludes them) and the *raw* kill score (which counts them as survivors).
Work to raise the adjusted score.

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
