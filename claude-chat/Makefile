IMAGE_NAME := europe-docker.pkg.dev/filiplindqvist-com-ea66d/images/claude-chat:latest

.PHONY: build push run dev

build:
	docker build -t $(IMAGE_NAME) .

push: build
	docker push $(IMAGE_NAME)

run:
	docker run --rm -p 3001:3001 --env-file ../.env $(IMAGE_NAME)

dev:
	npm run dev
