.PHONY: build-image run checks all test clean clean-mutants

CONTAINER_ENGINE := $(shell command -v podman 2> /dev/null || command -v docker 2> /dev/null)
IMAGE_NAME ?= avocado-agent-container
# Name of the container started by `make run`.
# Override it to run several containers at once, e.g. `make run CONTAINER_NAME=avocado-2`.
CONTAINER_NAME ?= avocado-agent

build-image:
	$(CONTAINER_ENGINE) build -t $(IMAGE_NAME) .

# Memory ceiling for the container.
# Parallelized CBMC runs in a container must not exhaust the host.
CONTAINER_MEMORY ?= 256g

run:
	$(CONTAINER_ENGINE) run -it --rm --name $(CONTAINER_NAME) \
	  --memory=$(CONTAINER_MEMORY) --memory-swap=$(CONTAINER_MEMORY) \
	  --oom-score-adj=500 \
	  -v $(PWD):/app $(IMAGE_NAME)

all: build-image test checks
test:
	uv run pytest

# Run all code style checks.
checks:
	prek -q run --all-files

# Delete artifacts from CBMC runs.
clean:
	find . \( -name '*.goto' -o -name '*callgraph.json' -o -name '*.jsonl' \) -delete
	find . -type d -name '__pycache__' -exec rm -rf {} +

# Delete artifacts from mutation testing runs.
clean-mutants:
	find . \( -name '*.goto' -o -name '*__mutant_*.c' \) -delete
	find . \( -name '*.goto' -o -name '*__clause_drop_*.c' \) -delete
