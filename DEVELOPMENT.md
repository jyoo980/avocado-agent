# Development work on Avocado Agent

This document captures possible features and improvements to Avocado Agent.

## Speed Enhancements

- The current system will invoke agents on a function multiple times, which also runs mutation
  testing multiple times. This is regardless of whether the mutants can be killed or not.
  - **Idea**: Give the agent an "escape hatch" to stop iterating on a function if there are mutants
    that cannot be killed.

## Limitations with `--depth`

- Agents often note in the code (or via their traces) that mutants cannot be killed due to the
  default `--depth` bound (200). Make it possible for agents to change the bound, but only to higher
  values (lowering a bound makes vacuous verification results more likely).

## `DFCC` Mode

- Running CBMC with `--dfcc` is likely what we want to do going forward. This requires a rework of
  the scripts we have to run CBMC, and likely a change to the prompts and documentation provided to
  agents to mention `--dfcc` mode and the harnesses it requires.
