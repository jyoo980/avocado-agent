#!/bin/sh

cd "$AVOCADO_AGENT_DIR" || exit
git clone "$SUBJECT_PROGRAM_REPO"
cd "$SUBJECT_PROGRAM_DIR" || exit
rm -rf .git .gitignore
mkdir -p .claude
cp -p ~/.claude/.credentials.json .claude/.credentials.json
if [ ! -f .claude.json ]; then echo '{}' > .claude.json; fi
temp_file=$(mktemp) && jq '.hasCompletedOnboarding = true' .claude.json > "$temp_file" && mv "$temp_file" .claude.json
temp_file=$(mktemp) && jq '.userID = (input | .userID)' .claude.json ~/.claude.json > "$temp_file" && mv "$temp_file" .claude.json
temp_file=$(mktemp) && jq '.oauthAccount = (input | .oauthAccount)' .claude.json ~/.claude.json > "$temp_file" && mv "$temp_file" .claude.json
cd ..
