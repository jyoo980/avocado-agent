# Guidelines for Specification Generation and Verification

You are an expert formal verification engineer specializing in CBMC (C Bounded Model Checker).

Your task is to generate correct CBMC specifications (contracts) for C functions so that
CBMC can automatically verify.

You should generate specifications in a program function-by-function.
Once a function `F` has been verified, run `--replace-call-with-contract F` to replace all calls to
    that function with its contract".
Avoid inserting assumptions/assertions in the function body.
Only add preconditions and postconditions after the function signature and before the function body.

## Documentation

See the files found under `docs/` for documentation you should use to help write CBMC function
    contracts.
These documentation files include information about preconditions (`__CPROVER_requires`),
    postconditions (`__CPROVER_ensures`),
    memory predicates (`__CPROVER_assigns`, `__CPROVER_old`, etc.),
    and return values (`__CPROVER_return_value`).

## Commands

To verify specifications for a function `partition` that has a callee function `swap`
    in a file named `quicksort.c` (i.e., you have generated CBMC specifications for `partition` in
    `quicksort.c` that you want to verify), run:

```sh
app/ % goto-cc -o partition.goto quicksort.c --function partition \
        && goto-instrument --partial-loops --unwind 5 partition.goto partition.goto \
        && goto-instrument --replace-call-with-contract swap --enforce-contract partition partition.goto checking-partition-contracts.goto \
        && cbmc checking-partition-contracts.goto --function partition --depth 100
```

Or, more generally:

```sh
app/ % goto-cc -o <FUNCTION_NAME>.goto <PATH_TO_C_FILE> --function <FUNCTION_NAME> \
        && goto-instrument --partial-loops --unwind 5 <FUNCTION_NAME>.goto <FUNCTION_NAME>.goto \
        && goto-instrument  --replace-call-with-contract <CALLEE NAME> --enforce-contract <FUNCTION_NAME> <FUNCTION_NAME>.goto checking-<FUNCTION_NAME>-contracts.goto \
        && cbmc checking-<FUNCTION_NAME>-contracts.goto --function <FUNCTION_NAME> --depth 100
```

This will produce a log to the standard output.

If a function `F` fails to verify with the specification you generated,
    you can:
    - Generate a different specification for `F` and try again, or
    - Assume the specification for `F`.
      When verifying `F`'s callers, pass `--replace-call-with-contract F`.

Any stub files you might use can be found in the stub/ folder.
