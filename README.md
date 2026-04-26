# Avocado Agent

This contains the top-level prompt,
    context,
    and dependencies used in an agentic approach for CBMC specification generation and verification.

## Running Avocado Agent

All required dependencies (i.e., an installation of CBMC,
    the [C Bounded Model Checker](https://www.cprover.org/cbmc/),
    the [Claude Code CLI](https://code.claude.com/docs/en/cli-reference))
    are specified in a Docker image.
Run the following command to build the image:

```sh
% make build-image
```

Once the container is successfully built,
  run:

```sh
% make run
```

And validate the `cbmc` and `claude` commands work.

## Requirements

- An active [Claude Pro or Max](https://support.claude.com/en/articles/11049762-choosing-a-claude-plan)
  subscription (required to run Claude Code).
