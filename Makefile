build-image:
	docker build -t avocado-cbmc-container .

run:
	docker run -it --rm -v $(PWD):/app avocado-cbmc-container
