# Specifying and Verifying C Programs

You are an expert formal verification engineer specializing in the CBMC (C
Bounded Model Checker) tool.

Your task is to edit C programs to insert CBMC specifications (contracts) that
CBMC can verify.  Ideally, when you are done, CBMC should succeed when run on
each function, one-by-one.

It may be OK if a few of the specifications you write do not verify, for two
reasons.  First, if a program is incorrect, CBMC will issue a warning.  Second,
CBMC cannot verify all correct C code.

This `CLAUDE.md` file and directory `docs/` contain basic information about
using CBMC.  CBMC is documented at https://diffblue.github.io/cbmc/index.html
which includes a [User Guide](https://diffblue.github.io/cbmc/user_guide.html)
and [The CPROVER
Manual](https://diffblue.github.io/cbmc/cprover-manual/index.html).

## Syntax of C function specifications (contracts)

Preconditions and postconditions are written after the function signature and
before the function body, as shown in files in the `docs/` folder.

The syntax includes:

### Function contracts

* Preconditions and postconditions: `__CPROVER_requires(bool cond)`, `__CPROVER_ensures(bool cond)`.
  Documented in `contracts-requires-ensures.md`.
* Pre-/post-conditions about function pointers: `bool __CPROVER_obeys_contract(void (*f)(void), void (*c)(void))`.
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

## Stub files

Any stub files you might use can be found in the `stub/` folder.

## How to run CBMC

Here is the sequence of commands to verify one function named `<FUNCTION_NAME>`.
The function calls two other functions, `<CALLEE1>` and `<CALLEE2>`.
The function is defined in file `<PATH_TO_C_FILE>`.

```sh
FUNCTION=<FUNCTION_NAME> \
goto-cc -o ${FUNCTION}.goto <PATH_TO_C_FILE> --function ${FUNCTION} \
&& goto-instrument --partial-loops --unwind 5 ${FUNCTION}.goto ${FUNCTION}.goto \
&& goto-instrument --replace-call-with-contract <CALLEE1> --replace-call-with-contract <CALLEE2> --enforce-contract ${FUNCTION} ${FUNCTION}.goto checking-${FUNCTION}-contracts.goto \
&& cbmc checking-${FUNCTION}-contracts.goto --function ${FUNCTION} --depth 100
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
&& cbmc checking-${FUNCTION}-contracts.goto --function ${FUNCTION} --depth 100
```
