#!/bin/sh

if [ -z "$SUBJECT_PROGRAM_DIR" ]; then
    echo SUBJECT_PROGRAM_DIR is not defined
    exit 2
fi
if [ -z "$SUBJECT_PROGRAM_C_FILE" ]; then
    echo SUBJECT_PROGRAM_C_FILE is not defined
    exit 2
fi
if [ -z "$SUBJECT_PROGRAM_REPO" ]; then
    echo SUBJECT_PROGRAM_REPO is not defined
    exit 2
fi
if [ -z "$TREATMENT_NAME" ]; then
    echo TREATMENT_NAME is not defined
    exit 2
fi

AVOCADO_AGENT_DIR="$(pwd)/avocado-agent-$SUBJECT_PROGRAM_DIR-$TREATMENT_NAME"
export AVOCADO_AGENT_DIR
if [ -d "$AVOCADO_AGENT_DIR" ]; then rm -rf "$AVOCADO_AGENT_DIR" || true; fi
if [ -d "$AVOCADO_AGENT_DIR" ]; then mv -f "$AVOCADO_AGENT_DIR" ~/tmp/DELETEME; fi
git clone -q git@github.com:jyoo980/avocado-agent --branch yoo/treatments/"$TREATMENT_NAME" --depth 1 "$AVOCADO_AGENT_DIR"
cd "$AVOCADO_AGENT_DIR" || exit
make build-image
docker run -it --rm -v "$(pwd):/app" -e SUBJECT_PROGRAM_DIR="$SUBJECT_PROGRAM_DIR" -e SUBJECT_PROGRAM_C_FILE="$SUBJECT_PROGRAM_C_FILE" -e IS_SANDBOX=1 avocado-agent-container
cd ..
