.PHONY: build-image run checks test clean

build-image:
	docker build -t avocado-agent-container .

run:
	docker run -it --rm -v $(PWD):/app avocado-agent-container

