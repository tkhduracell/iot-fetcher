IMAGE_NAME := europe-docker.pkg.dev/filiplindqvist-com-ea66d/images/ai-brain:latest

.PHONY: build push test dev

build:
	docker build -t $(IMAGE_NAME) .

push: build
	docker push $(IMAGE_NAME)

test:
	uv run pytest -q

dev:
	docker run --rm --env-file .env -e DRY_RUN=1 -v $(PWD)/../volumes/ai-brain-memory:/memory $(IMAGE_NAME)
