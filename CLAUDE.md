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
The main documentation can be found at at https://diffblue.github.io/cbmc/index.html,
which includes a [User Guide](https://diffblue.github.io/cbmc/user_guide.html)
and [The CPROVER Manual](https://diffblue.github.io/cbmc/cprover-manual/index.html).
You can also search the web for more CBMC documentation.

## Mutation Testing 

- **To get the mutants of a function you must try to kill**, run:

  ```sh
  avocado-get-mutants --function <FUNCTION_NAME> \
                   --file <PATH_TO_C_FILE> \
  ```
  If there are mutants for the function, exits with status 0 and prints them to the stdout as unified diffs.
  If there are no mutants for the function, exits with status 0 and prints "No mutant(s) generated" to stdout.

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
