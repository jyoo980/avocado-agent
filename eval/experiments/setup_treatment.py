#!/usr/bin/env python
"""Executable script to set up per-treatment clones of the `avocado-agent` repository.

A treatment corresponds to a configuration of the `avocado-agent` system with or without components
enabled (e.g., tools, curated documentation, mutation testing). Treatments exist as branches on
the `avocado-agent` repository. For example, `avocado-agent` with mutation testing removed (i.e.,
ablated) lives at `yoo/treatments/avocado-minus-mutation-testing`.

This script must be run with a `BACKEND_MODEL` argument which specifies what LLM drives the harness.
An optional `--treatment` argument may also be supplied.

To set up the full set of treatments using `opus-4-8` as the backend model, a user can run:

    % uv run python3 eval/experiments/setup_treatment.py opus-4-8

This will clone all treatments into a subdirectory named `opus-4-8` where the script is executed,
e.g.,
    opus-4-8/
        pure-agentic-baseline/
        full-avocado/
        avocado-minus-docs/
        avocado-minus-tools/
        avocado-minus-mutation-testing/
        avocado-minus-harness/
        avocado-minus-tools-minus-docs/

To clone a single treatment for a model (e.g., the -docs treatment for opus-5-5), you can run:

    % uv run python3 eval/experiments/setup_treatment.py opus-5-5 --treatment avocado-minus-docs

Usage:
    % uv run python3 eval/experiments/setup_treatment.py BACKEND_MODEL [--treatment]

"""

import argparse
import shlex
import subprocess
import sys

from pathlib import Path

AVOCADO_AGENT_GIT_URL = "git@github.com:jyoo980/avocado-agent.git"

AVOCADO_BACKEND_MODELS = [
    "opus-4-8",
    "opus-5-5",
    "qwen-3-8",
]

TREATMENT_PREFIX = "yoo/treatments"
TREATMENT_NAMES = [
    "pure-agentic-baseline",
    "full-avocado",
    "avocado-minus-docs",
    "avocado-minus-tools",
    "avocado-minus-mutation-testing",
    "avocado-minus-harness",
    "avocado-minus-tools-minus-docs",
]

def main() -> None:
    """Clone the requested treatment branches into a directory named after the backend model."""
    args = _get_args()
    treatments = TREATMENT_NAMES if args.treatment is None else [args.treatment]
    _create_treatment_dirs(args.model, treatments)

def _get_args() -> argparse.Namespace:
    """Parse the command-line arguments.

    Returns:
        The parsed arguments: ``model``, the backend model, and ``treatment``, a single
        treatment name or None to set up every treatment.
    """
    parser = argparse.ArgumentParser(
        description="Set up the experimental treatment directories for avocado-agent"
    )
    parser.add_argument(
        "model",
        help="The backend model for a treatment",
        choices=AVOCADO_BACKEND_MODELS
    )
    parser.add_argument(
        "--treatment",
        choices=TREATMENT_NAMES

    )
    return parser.parse_args()

def _get_clone_command(backend_model: str, treatment: str) -> list[str]:
    """Build the git clone command for one of a backend model's treatments.

    The command clones the treatment's branch into ``<backend_model>/<treatment>``.

    Args:
        backend_model: The backend model the treatment is for.
        treatment: The treatment to clone.

    Returns:
        The command, as an argument vector.
    """
    return [
        "git",
        "clone",
        "-b",
        f"{TREATMENT_PREFIX}/{treatment}",
        AVOCADO_AGENT_GIT_URL,
        f"{backend_model}/{treatment}",
    ]

def _create_treatment_dirs(backend_model: str, treatments: list[str]) -> None:
    """Clone each treatment into ``<backend_model>/<treatment>`` under the working directory.

    The backend model's directory may already exist, for example from setting up another
    treatment for the same model, but none of the treatments' directories may.

    Args:
        backend_model: The backend model, used as the directory name.
        treatments: The treatments to clone.

    Raises:
        SystemExit: If a treatment's directory already exists or a clone fails.
    """
    model_dir = Path.cwd() / backend_model
    existing = [str(model_dir / t) for t in treatments if (model_dir / t).exists()]
    if existing:
        message = f"Treatment directories already exist: {', '.join(existing)}"
        raise SystemExit(message)

    model_dir.mkdir(parents=True, exist_ok=True)

    failed = []
    for treatment in treatments:
        cmd = _get_clone_command(backend_model, treatment)
        clone_result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if clone_result.returncode != 0:
            print(f"Error while running: {shlex.join(cmd)}\n{clone_result.stderr}", file=sys.stderr)
            failed.append(treatment)
    if failed:
        message = f"Failed to clone treatments: {', '.join(failed)}"
        raise SystemExit(message)

if __name__ == "__main__":
    main()
