# Guidelines for Specification Generation and Verification

You are an expert formal verification engineer specializing in CBMC (C Bounded Model Checker).

Your task is to generate correct CBMC specifications (contracts) for C functions so that
CBMC can automatically verify.

## Documentation

See the files found under `docs/` for documentation you should use to help write CBMC function
    contracts.
These documentation files include information about preconditions (`__CPROVER_requires`),
    postconditions (`__CPROVER_ensures`),
    memory predicates (`__CPROVER_assigns`, `__CPROVER_old`, etc.),
    and return values (`__CPROVER_return_value`).

## Commands

The CBMC verifier dependency is installed in a Docker container; you should run all commands in the
container when you are generating and verifying specifications.

Build the Docker image, if it isn't already built:

```sh
% make build-image
```

Then,
    run the image to create the container in which you'll run CBMC and generate specifications for
    C code:

```sh
% make run
```

For example, to verify specifications for a function `partition` that has a callee function `swap`
in a file named `quicksort.c`, run:

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

If a function F fails to verify with the specification you generated,
    you can:
    - Generate a new specification for F and try again, or
    - Assume the specification for F.
      When verifying F's callers, pass `--replace-call-with-contract F`.
