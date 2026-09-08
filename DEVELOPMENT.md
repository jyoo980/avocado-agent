# Development work on Avocado Agent

This document captures basic development and contribution guidelines for Avocado Agent,
    in addition to possible features and improvements.

## Contributing to Avocado Agent

As a contributor to Avocado Agent,
    you will either work off your own fork of the system,
    or be granted read/write access.
The default workflow is to work off a fork;
    please contact one of the current maintainers if you believe you should be granted read/write
    access.

All contributions should be implemented in a separate branch off `main`.
Please feel free to open a pull request (draft pull requests are fine,
    and encouraged since CI will run on them) for feedback.

## Possible Feature Enhancements and Tasks

### Speed Enhancements

- The current system will invoke agents on a function multiple times, which also runs mutation
  testing multiple times. This is regardless of whether the mutants can be killed or not.
  - **Idea**: Give the agent an "escape hatch" to stop iterating on a function if there are mutants
    that cannot be killed.

### Limitations with `--depth`

- Agents often note in the code (or via their traces) that mutants cannot be killed due to the
  default `--depth` bound (200). Make it possible for agents to change the bound, but only to higher
  values (lowering a bound makes vacuous verification results more likely).

### `DFCC` Mode

- Running CBMC with `--dfcc` is likely what we want to do going forward. This requires a rework of
  the scripts we have to run CBMC, and likely a change to the prompts and documentation provided to
  agents to mention `--dfcc` mode and the harnesses it requires.
