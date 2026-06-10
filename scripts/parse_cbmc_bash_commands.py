#!/usr/bin/env -S uv run --quiet python3

"""Extract CBMC commands from a Claude Code trace JSONL file.

Usage:
    % ./scripts/parse_cbmc_bash_commands.py

Claude Code saves the conversation from each session in `~/.claude/projects` as a `.jsonl` file.
Each line of the file is a JSON object. Below is a partial example:

    {
      "parentUuid": "c751f33e-7543-4824-a29f-d8b5833162d2",
      "isSidechain": false,
      "message": {
        "model": "claude-opus-4-8",
        "id": "msg_01MNSk1ExTvHPsQyqc8YTvEu",
        "type": "message",
        "role": "assistant",
        "content": [
          {
            "type": "tool_use",
            "id": "toolu_01Cs85kjwTHjuJ61j6QkcBZz",
            "name": "Bash",
            "input": {
              "command": "cd /tmp; TAIL=60 /tmp/iv.sh ZopfliVerifyLenDist 2>&1 ",
              "description": "Re-verify VerifyLenDist with invariants"
            },
            "caller": {
              "type": "direct"
            }
          }
        ],
        ...
    }

This script extracts CBMC commands from a conversation record.
"""

import json
import re
import sys
from pathlib import Path

_CBMC_COMMAND_PATTERN = re.compile(r"\b(goto-cc|goto-instrument|cbmc)\b")


def main() -> None:
    """Extract CBMC commands from a Claude Code trace JSONL file."""
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <trace.jsonl>", file=sys.stderr)
        sys.exit(1)

    cbmc_commands = _parse_cbmc_bash_commands(sys.argv[1])
    for i, cmd in enumerate(cbmc_commands):
        print(json.dumps({"index": i, "command": cmd}))


def _parse_cbmc_bash_commands(path_to_claude_code_conversation_record: str) -> list[str]:
    """Return the CBMC commands parsed from an entire Claude Code conversation record.

    Args:
        path_to_claude_code_conversation_record (str): Path to the Claude Code conversation record.

    Returns:
        list[str]: The CBMC commands parsed from an entire Claude Code conversation record.
    """
    cbmc_commands = []
    raw_claude_jsonl = [
        line
        for raw_line in Path(path_to_claude_code_conversation_record)
        .read_text(encoding="utf-8")
        .splitlines()
        if (line := raw_line.strip())
    ]
    for line in raw_claude_jsonl:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            print(f"Error parsing JSON from: {line}")
            continue

        if _is_assistant_message(entry):
            tool_call_commands = _get_bash_tool_call_commands(entry.get("message"))
            cbmc_commands = [cmd for cmd in tool_call_commands if _CBMC_COMMAND_PATTERN.search(cmd)]
    return cbmc_commands


def _is_assistant_message(entry: dict) -> bool:
    """Return True iff the given entry from a Claude Code conversation log is from the assistant.

    Args:
        entry (dict): The entry to check.

    Returns:
        bool: True iff the entry from a Claude Code conversation log is from the assistant.
    """
    message = entry.get("message")
    if not message:
        return False
    if role := message.get("role"):
        return role == "assistant"
    return False


def _get_bash_tool_call_commands(assistant_message: dict) -> list[str]:
    """Return all Bash tool call commands from an assistant message.

    Args:
        assistant_message (dict): The assistant message.

    Returns:
        list[str]: All Bash tool call commands from an assistant message.
    """
    bash_tool_calls = []
    content = assistant_message.get("content")
    if not content:
        return bash_tool_calls
    bash_tool_calls = [
        block.get("input", {}).get("command")
        for block in content
        if block.get("type") == "tool_use" and block.get("name") == "Bash"
    ]
    if any(not command for command in bash_tool_calls):
        msg = f"'command' had no value in a Bash tool call by Claude: {assistant_message}"
        raise ValueError(msg)
    return bash_tool_calls


if __name__ == "__main__":
    main()
