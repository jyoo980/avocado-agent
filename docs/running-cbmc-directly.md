## How to run CBMC directly

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