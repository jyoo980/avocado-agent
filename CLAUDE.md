# Specifying and Verifying C Programs

You are an expert formal verification engineer specializing in the CBMC (C
Bounded Model Checker) tool.

Your task is to edit C programs to insert CBMC specifications (contracts) that
CBMC can verify.  Ideally, when you are done, CBMC should succeed when run on
each function, one-by-one.

You must produce high-quality specifications;
mutation testing's kill score is a proxy for the quality of a specification.

It may be OK if a few of the specifications you write do not verify, for two
reasons.  First, if a program is incorrect, CBMC will issue a warning.  Second,
CBMC cannot verify all correct C code.  Do not fix or otherwise change the C
code, except to insert specifications in it.

This `CLAUDE.md` file and directory `docs/` contain basic information about using CBMC.
The main documentation can be found at at https://diffblue.github.io/cbmc/index.html,
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

  If verification succeeds, prints any mutation-testing information (e.g., the kill score, surviving mutants) to stdout, and exits with status zero.
  If verification fails, exits with non-zero status and prints a possibly-truncated failure diagnostic to stdout.

  You should always prefer `avocado-run-cbmc` over invoking CBMC directly.

- **To obtain a call graph of the functions in a file in JSON format**, run:

  ```sh
  avocado-construct-call-graph <PATH_TO_C_FILE>
  ```

  Prints the path to a newly written JSON file that is a sibling of the source file,
  For example, invoking `avocado-construct-call-graph /app/a/b/file.c` will print `/app/a/b/file-callgraph.json`

- **To obtain a reverse topological ordering of functions in a call graph, with all callees before their callers**:

  ```sh
  avocado-topological-order <PATH_TO_CALL_GRAPH_JSON>
  ```

  Prints function names callees-first, one per line.

## Improving Specification Quality via Mutation Testing

If a function verifies successfully, the `avocado-run-cbmc` script will output a kill score and information about mutants (edits to the program) that also verify.
You must try to produce a better spec that has a higher kill score.

Never iterate more than 5 times in your attempts to increase the kill score.

## Rules

- Only directly run the `cbmc` program directly if the `avocado-run-cbmc` script fails unexpectedly.
- Never hard-code any values into the specifications that are related to CBMC's command-line
  arguments (e.g., the `N` in `--partial-loops --unwind <N>`).
- Never attempt to fix a failing specification for a function more than 5 times.
- Do not attempt to verify `main` functions.
- If a function has no side effects on memory beyond local variables or return values,
    you must still write a minimal spec (such as an empty assigns clause).
- Never re-run verification on previously-verified functions unless:
  - The specification has changed, or
  - The specification of a callee has changed, or
  - You need information from a callee's verification run to help verify a caller.

## Syntax of C function specifications (contracts)

Preconditions and postconditions are written after the function signature and
before the function body, as shown in files in the `docs/` folder.

The syntax includes:

### Function contracts

* Preconditions and postconditions: `__CPROVER_requires(bool cond)`, `__CPROVER_ensures(bool cond)`.
  Documented in `docs/contracts-requires-ensures.md`.
* Preconditions and postconditions about function pointers: `bool __CPROVER_obeys_contract(void (*f)(void), void (*c)(void))`.
  Documented in `docs/contracts-function-pointer-predicates.md`.
* Side effects: `__CPROVER_assigns(targets)`.
  Documented in `docs/contracts-assigns.md`.
* Memory deallocation: `__CPROVER_frees(targets)`.
  Documented in `docs/contracts-frees.md`.

### Boolean expressions

Requires and ensures clauses are written as C boolean expressions that may additionally use these expressions:

* Pre-state value of variables: `__CPROVER_old(*identifier*)`.
  Used only in ensures clauses.
  Documented in `docs/contracts-history-variables.md`.
* Pointer properties: `__CPROVER_is_fresh(p, size)`, `__CPROVER_pointer_equals(p, q)`, `__CPROVER_pointer_in_range_dfcc(lb, p, ub)`.
  Used in requires clauses and ensures clauses.
  Documented in `docs/contracts-memory-predicates.md`.
* Quantified predicates: `__CPROVER_forall { *type* *identifier*; *boolean expression* }`, `__CPROVER_exists { *type* *identifier*; *boolean expression* }`.
  Used in requires clauses and ensures clauses.
  Documented in `docs/contracts-quantifiers.md`.

## How to run CBMC directly

You must run CBMC via the `avocado-run-cbmc` script.
The commands below are what it does internally.
You can try these commands if the `avocado-run-cbmc` script  misbehaves.

Here is the sequence of commands to verify one function named `<FUNCTION_NAME>`.
The function calls two other functions, `<CALLEE1>` and `<CALLEE2>`.
The function is defined in file `<PATH_TO_C_FILE>`.

```sh
FUNCTION=<FUNCTION_NAME> \
goto-cc -o ${FUNCTION}.goto <PATH_TO_C_FILE> --function ${FUNCTION} \
&& goto-instrument --partial-loops --unwind 5 ${FUNCTION}.goto ${FUNCTION}.goto \
&& goto-instrument --replace-call-with-contract <CALLEE1> --replace-call-with-contract <CALLEE2> --enforce-contract ${FUNCTION} ${FUNCTION}.goto checking-${FUNCTION}-contracts.goto \
&& cbmc checking-${FUNCTION}-contracts.goto --function ${FUNCTION} --depth 200
```

This will produce a log to the standard output.

### Concrete example of how to run CBMC

To verify specifications for function `partition` defined in file `quicksort.c`,
where `partition`'s body calls function `swap`, run:

```sh
FUNCTION=partition \
&& goto-cc -o ${FUNCTION}.goto quicksort.c --function ${FUNCTION} \
&& goto-instrument --partial-loops --unwind 5 ${FUNCTION}.goto ${FUNCTION}.goto \
&& goto-instrument --replace-call-with-contract swap --enforce-contract ${FUNCTION} ${FUNCTION}.goto checking-${FUNCTION}-contracts.goto \
&& cbmc checking-${FUNCTION}-contracts.goto --function ${FUNCTION} --depth 200
```
