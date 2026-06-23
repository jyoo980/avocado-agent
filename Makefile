.PHONY: build-image run

build-image:
	docker build -t avocado-agent-container .

run:
	docker run -it --rm -v $(PWD):/app avocado-agent-container

