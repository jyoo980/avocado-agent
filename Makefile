.PHONY: build-image run checks all test clean clean-mutants

IMAGE_NAME ?= avocado-agent-container
# Name of the container started by `make run`.
# Override it to run several containers at once, e.g. `make run CONTAINER_NAME=avocado-2`.
CONTAINER_NAME ?= avocado-agent

build-image:
	docker build -t $(IMAGE_NAME) .

run:
	docker run -it --rm --name $(CONTAINER_NAME) -v $(PWD):/app $(IMAGE_NAME)

test:
	uv run pytest

# Run all code style checks.
checks: style-fix style-check

# Delete artifacts from CBMC runs.
clean:
	find . \( -name '*.goto' -o -name '*callgraph.json' -o -name '*.jsonl' \) -delete
	find . -type d -name '__pycache__' -exec rm -rf {} +

# Delete artifacts from mutation testing runs.
clean-mutants:
	find . \( -name '*.goto' -o -name '*__mutant_*.c' \) -delete
	find . \( -name '*.goto' -o -name '*__clause_drop_*.c' \) -delete
