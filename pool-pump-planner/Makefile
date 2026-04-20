IMAGE := europe-docker.pkg.dev/filiplindqvist-com-ea66d/images/pool-pump-planner:latest

build:
	docker build -t $(IMAGE) .

push: build
	gcloud auth configure-docker europe-docker.pkg.dev
	docker push $(IMAGE)

run: build
	docker run --rm --env-file ../fetcher-core/python/.env $(IMAGE) --once

test:
	go vet ./...
	go test ./...

.PHONY: build push run test
