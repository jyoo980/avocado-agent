# Avocado Agent

This contains the top-level prompt,
    context,
    and dependencies used in an agentic approach for CBMC specification generation and verification.

## Running Avocado Agent

All required dependencies (i.e., an installation of CBMC,
    the [C Bounded Model Checker](https://www.cprover.org/cbmc/)) are installed in a Docker
    container,
    which must first be built:

```sh
% make docker-build
```

Once the container is successfully built,
    

## Requirements

- [Claude Code](https://code.claude.com/docs/en/overview)