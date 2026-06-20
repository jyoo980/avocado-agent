# Experiments with Avocado Agent

## Procedure

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

3. Inside the container, run the following to enable Claude to run with `--dangerously-skip-permissions` enabled:
    ```sh
    export IS_SANDBOX=1
    ```
    Then, run the following command to kick off specification generation and capture output:

    ```sh
    % claude "Carefully read @CLAUDE.md. Then, verify the C source files in <REPO_PATH>" --dangerously-skip-permissions --print --output-format json > claude-output-<REPO_PATH>.json
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
