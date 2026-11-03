#!/usr/bin/env python

import argparse
import subprocess

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
    clone_cmds = _get_clone_commands(args.model, args.treatment)
    _create_treatment_dir(args.model, clone_cmds)

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

def _get_clone_commands(backend_model: str, treatment: str | None) -> list[str]:
    """Build the git clone commands for a backend model's treatments.

    Each command clones the treatment's branch into ``<backend_model>/<treatment>``.

    Args:
        backend_model: The backend model the treatments are for.
        treatment: The treatment to clone, or None to clone every treatment in TREATMENT_NAMES.

    Returns:
        One shell command string per treatment.
    """
    clone_cmds = []
    if treatment is None:
        clone_cmds = [
            f"git clone -b {TREATMENT_PREFIX}/{treatment} {AVOCADO_AGENT_GIT_URL} {backend_model}/{treatment}"
            for treatment in TREATMENT_NAMES
        ]
    else:
        clone_cmds.append(
            f"git clone -b {TREATMENT_PREFIX}/{treatment} {AVOCADO_AGENT_GIT_URL} {backend_model}/{treatment}"
        )
    return clone_cmds

def _create_treatment_dir(backend_model: str, clone_cmds: list[str]) -> None:
    """Create the backend model's directory under the working directory and run the clone commands.

    Args:
        backend_model: The backend model, used as the directory name.
        clone_cmds: Shell commands that clone each treatment into the new directory.

    Raises:
        Exception: If the backend model's directory already exists.
    """
    working_dir = Path.cwd()

    treatment_dir = working_dir / backend_model
    if treatment_dir.exists():
        raise Exception(f"A treatment directory named '{treatment_dir}' already exists")

    treatment_dir.mkdir(parents=True, exist_ok=True)

    for cmd in clone_cmds:
        clone_result = subprocess.run(cmd, capture_output=True, text=True, shell=True)
    
if __name__ == "__main__":
    main()
