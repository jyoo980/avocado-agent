# Experiments with Avocado Agent

## Repeated, isolated runs (recommended)

`eval/experiments/run.sh` runs Avocado on a subject program *n* times, each in a fresh
container, so no run can see another's specs, logs, Claude Code session transcripts or memory:

- Each run starts a new `--rm` container; `~/.claude` (via a throwaway `CLAUDE_CONFIG_DIR`),
  `/app`, and any stray `cbmc` processes die with it.
- Each run gets its own copy of the subject, taken from the pristine `--subject` directory and
  scrubbed of `*.goto`, `*-callgraph.json`, `*.jsonl`, mutant files, etc.
- The repository is mounted read-only at `/src` and copied into the container-private `/app`.
- Results are written to the **host**, outside the repository and outside every container, so
  they persist after each container exits: by default
  `~/.local/share/avocado-experiments/<tag>/<timestamp>/run-<i>/` (`subject/`,
  `avocado-verify.log`, `exit-code`), plus a `batch-info.txt` recording the harness commit and
  parameters. Override the location with `--results <dir>` or `AVOCADO_RESULTS_DIR`. Each run's
  `subject/` is bind-mounted into its container, so partial logs survive even an OOM kill.
- `--memory`/`--cpus` cap each container, so an OOM-killed CBMC only costs that one run, and
  `-j` runs several containers at once.

### One-time login

Pick one auth profile under `eval/experiments/profiles/` (the `*.env` files are gitignored):

- **Claude subscription**: run `eval/experiments/login.sh` once; it prints a long-lived OAuth
  token. Copy `subscription.env.example` to `subscription.env` and paste the token in.
  (Alternatively `login.sh volume` stores an interactive `claude auth login` on a named volume
  that every run seeds its fresh config directory from.)
- **LiteLLM-hosted model**: copy `litellm.env.example` to `litellm.env` and set the proxy URL,
  key, and the model name (forwarded to `claude --model`, overriding `.claude/settings.json`).

### Launching

```sh
% eval/experiments/run.sh --subject path/to/subject --file src/foo.c \
      --profile eval/experiments/profiles/subscription.env -n 5 --memory 32g
```

`--file` is relative to `--subject`. Add `-j 2` to run two containers at a time, `--tag` to name
the batch, and `--claude-timeout` to forward a per-function timeout to `avocado-verify`.

## Procedure (manual, single run)

1. Clone the repository containing the C files you want to generate specs for directly into the
  `avocado-agent` repository at the top-level.
2. Ensure you can build + deploy the Docker container in which Avocado does work:

    ```sh
    % make build-image # Builds the image.
    ```

    ```sh
    % make run # Runs the container with the image you built.
    ```

    **All operations from this point on occur within the container**.

3. Run the following command to kick off specification generation and capture output:

    ```sh
    % avocado-verify --file <PATH_TO_C_FILE>
    ```

4. Save the output and metadata with the `save-session-metadata` script (inside the container):

    ```sh
    % save-session-metadata claude-output-<REPO_PATH>.json <REPO_PATH>
    ```

## Treatments

- [x] [Baseline Avocado Agent](https://github.com/jyoo980/avocado-agent/tree/24e1aacefab0845e79b3c638e428de7463a1e2ae)
  - No mutation testing tool access, this is the baseline.
- [x] [Avocado Agent with Mutation Testing tool access](https://github.com/jyoo980/avocado-agent/tree/148284c6d426909486a962d399ca9e0e92b666ae)
  - Currently the treatment with the highest kill score.
- [x] [Avocado Agent with directive to produce the strongest specification](https://github.com/jyoo980/avocado-agent/tree/9497e441ff5e8bec74c7506d9bebae878a94a931)
  - Treatment with the second-highest kill score.
- [x] Avocado Agent with directive to focus on generating postconditions.
  - This did not increase the kill score, and often resulted in postconditions that effectively reproduced the method body verbatim.
- [ ] Avocado Agent with access to the mutation testing tool *and* directive to produce the strongest specification.
- [ ] Avocado Agent without *any* tools (e.g., no access to any of the scripts under [`tools`](./../tools).)
