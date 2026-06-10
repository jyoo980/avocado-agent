"""Extract cbmc commands from a Claude Code trace JSONL file."""

import json
import re
import sys

TOOL_PATTERN = re.compile(r'\b(goto-cc|goto-instrument|cbmc)\b')


def extract_bash_commands(filepath):
    commands = []
    with open(filepath, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Look for assistant messages containing tool_use with name "Bash"
            message = entry.get("message", {})
            if not isinstance(message, dict):
                continue
            content = message.get("content", [])
            if not isinstance(content, list):
                continue
            for block in content:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_use"
                    and block.get("name") == "Bash"
                ):
                    command = block.get("input", {}).get("command")
                    if command and TOOL_PATTERN.search(command):
                        commands.append(command)
    return commands


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <trace.jsonl>", file=sys.stderr)
        sys.exit(1)

    commands = extract_bash_commands(sys.argv[1])
    for i, cmd in enumerate(commands):
        print(json.dumps({"index": i, "command": cmd}))
