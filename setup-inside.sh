#!/bin/sh

cd /app/"$SUBJECT_PROGRAM_DIR" || exit
temp_file=$(mktemp) && jq '.projects["'"$(pwd)"'"].hasTrustDialogAccepted = true' ~/.claude.json > "$temp_file" && mv "$temp_file" ~/.claude.json
temp_file=$(mktemp) && jq '.projects["'"$(pwd)"'"].hasCompletedProjectOnboarding = true' ~/.claude.json > "$temp_file" && mv "$temp_file" ~/.claude.json
