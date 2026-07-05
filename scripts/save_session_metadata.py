#!/usr/bin/env -S uv run --quiet python3

"""Save the Claude Code data for a specification generation session.

Usage:
    % save-session-metadata <PATH_TO_CLAUDE_OUTPUT_JSON>
"""

import argparse
import json
import shutil
from pathlib import Path

from loguru import logger


def main() -> None:
    """Save the Claude Code data for a specification generation session."""
    parser = argparse.ArgumentParser(
        description=("Save the Claude Code session file for a corresponding conversation trace.")
    )
    parser.add_argument(
        "claude_output",
        help="The Claude output file.",
    )
    parser.add_argument(
        "target_repository",
        help="The repository in which specifications were generated.",
    )
    args = parser.parse_args()
    _save_session_files(args.claude_output, args.target_repository)


def _save_session_files(claude_output: str, target_repository: str) -> None:
    """Copy a run's Claude Code session file and output JSON into the target repository.

    Args:
        claude_output (str): Path to the Claude output JSON file whose session ID
            names the session file to copy.
        target_repository (str): Path to the repository in which specifications were
            generated, into which the files are copied.
    """
    path_to_claude_output = Path(claude_output)
    claude_output_data = None
    try:
        claude_output_data = json.loads(path_to_claude_output.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.error(f"Failed to parse JSON from {claude_output}")
    if not claude_output_data:
        logger.error(f"Error obtaining Claude run metadata from '{claude_output}'")
        return
    # Expand the home shorthand (~) so existence checks work correctly
    claude_session_file_folder = Path("~/.claude/projects/-app").expanduser()
    if not claude_session_file_folder.exists():
        msg = f"No '{claude_session_file_folder}' (Claude Session folder) present"
        raise ValueError(msg)

    claude_session_file = claude_session_file_folder / f"{claude_output_data['session_id']}.jsonl"
    if not claude_session_file.exists():
        logger.error(f"Claude session file not found: {claude_session_file}")
        return

    target_repo_path = Path(target_repository)
    if not target_repo_path.exists():
        logger.error(f"Target repository path does not exist: {target_repo_path}")
        return

    try:
        shutil.copy2(claude_session_file, target_repo_path)
        shutil.copy2(path_to_claude_output, target_repo_path)
        logger.info(f"Copied Claude session file {claude_session_file} to {target_repo_path}/")
        logger.info(f"Copied Claude session output {claude_output} to {target_repo_path}/")
    except OSError:
        logger.exception("Failed to copy Claude session file")


if __name__ == "__main__":
    main()
