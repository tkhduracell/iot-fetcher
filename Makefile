
build: 
	docker build -t europe-docker.pkg.dev/filiplindqvist-com-ea66d/images/iot-fetcher:latest .

push: build
	gcloud auth configure-docker europe-docker.pkg.dev
	docker push europe-docker.pkg.dev/filiplindqvist-com-ea66d/images/iot-fetcher:latest

run:
	docker build . -t iot-fetcher:latest && docker run --rm -p 8080:8080 --env-file .env iot-fetcher:latest

dev:
	docker build . -t iot-fetcher:latest-dev --build-arg PYDEBUGGER=1 && docker run -e PYDEBUGGER=1 -p 5678:5678 --rm -p 8080:8080 --env-file .env iot-fetcher:latest-dev

.PHONY: build push run
