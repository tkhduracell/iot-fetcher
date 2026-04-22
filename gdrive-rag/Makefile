IMAGE := europe-docker.pkg.dev/filiplindqvist-com-ea66d/images/gdrive-rag:latest

build:
	docker build -t $(IMAGE) .

push: build
	gcloud auth configure-docker europe-docker.pkg.dev
	docker push $(IMAGE)

test:
	go vet ./...
	go test ./...

.PHONY: build push test
