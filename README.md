# Avocado Agent

This contains the top-level prompt,
    context,
    and dependencies used in an agentic approach for CBMC specification generation and verification.

The base requirements for running Avocado Agent is documented in the [requirements](#requirements)
    section.
Once the dependencies described there are installed,
    you can run Avocado Agent (see [running Avocado Agent](#running-avocado-agent)).

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
% make run # Deploys a container named `avocado-agent`
```

Or,
    to deploy a container with a custom name (useful for running multiple instances),
    run:

```sh
% make run CONTAINER_NAME=<CUSTOM_NAME>
```

The container entrypoint runs `uv sync --frozen` and prepends `.venv/bin` to
`PATH` automatically, so the avocado tools (`avocado-construct-call-graph`,
`avocado-topological-order`, `avocado-run-cbmc`) are directly callable in
the shell. Validate that `cbmc` and `claude` are also on `PATH`.

Running outside Docker still requires manually invoking `uv sync` and
either activating the venv or prefixing tool calls with `uv run`.

### Generating Specifications with Avocado Agent

Once the Avocado Agent container is running, use the following command (from inside the
    running container) to generate specifications for functions in a C file:

```sh
% avocado-verify --file <PATH_TO_C_FILE>
```

For example, to generate and verify specifications for a file `test.c` located in
    eval/benchmarks/ (this is an example), you would run:

```sh
% avocado-verify --file eval/benchmarks/test.c
```

from the application root of the container (i.e., `/app`)

### Container Configuration

There may be instances where the container requires additional memory or swap (e.g.,
    for a long-running `goto-instrument` or `cbmc` process).
Get started by obtaining the container id;
    Assuming you have a single container deployed from the Avocado Agent image,
    run:
    ```sh
    % docker ps -qf "ancestor=avocado-agent-container"
    ```

Check the memory and/or CPU limits for the container by running:

```sh
% docker stats <CONTAINER_ID>
```

he limits,
    by default,
    should match the maximum amount of CPU cores or RAM on the machine on which the container is
    running.
If this is not the case (e.g., for macOS),
    you can manually set the global VM limits via Docker Desktop by selecting `Settings -> Resources`.
Re-run the `docker stats` command afterwards to validate your changes have taken place.

## Requirements

- An active [Claude Pro or Max](https://support.claude.com/en/articles/11049762-choosing-a-claude-plan)
  subscription (required to run Claude Code).
- [Docker](https://www.docker.com): Avocado Agent and its dependencies (e.g., CBMC) are packaged and
  run inside a Docker container.
- [uv](https://docs.astral.sh/uv/)
  - Used to manage packages,
    dependencies,
    and develop the tools provided to Claude Code.

## DARPA TRACTOR Test Cases

Public test cases from the [DARPA TRACTOR](https://www.darpa.mil/research/programs/translating-all-c-to-rust)
  program are included as a Git submodule.
To obtain a local copy, run:

```sh
% git submodule update --init
```

## FAQ

> How do I run Claude Code with the `--dangerously-skip-permissions` flag in a Docker container?

This is not a recommended modality in which to run Claude Code;
  do so at your own risk.
If you are in a sandboxed environment with no internet access (e.g., a Docker container with limited
  network access),
  you can run:

```sh
% export IS_SANDBOX=1
```

And then run:

```sh
% claude --dangerously-skip-permissions
```

> My CBMC processes (e.g., `goto-instrument`, `cbmc`) keep erroring out with the error messsage "Killed"

This is most likely due to an OOM error.
See [the container configuration section](#container-configuration) for details.
