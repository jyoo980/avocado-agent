.PHONY: build-image run checks clean

build-image:
	docker build -t avocado-agent-container .

run:
	docker run -it --rm -v $(PWD):/app avocado-agent-container

# Run all code style checks.
checks: style-fix style-check

# Delete artifacts from CBMC runs.
clean:
	find . \( -name '*.goto' -o -name '*callgraph.json' -o -name '*.jsonl' \) -delete

# Code style; defines `style-check` and `style-fix`.
CODE_STYLE_EXCLUSIONS_USER := --exclude-dir test --exclude-dir data --exclude-dir docs --exclude CLAUDE.md
ifeq (,$(wildcard .plume-scripts))
dummy := $(shell git clone --depth=1 -q https://github.com/plume-lib/plume-scripts.git .plume-scripts)
endif
include .plume-scripts/code-style.mak

# ${PYTHON_FILES} is defined by the above style checking.
TAGS: tags
tags:
	etags ${PYTHON_FILES}
