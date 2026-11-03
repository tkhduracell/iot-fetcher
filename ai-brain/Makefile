IMAGE_NAME := europe-docker.pkg.dev/filiplindqvist-com-ea66d/images/ai-brain:latest

.PHONY: build push test dev report

build:
	docker build -t $(IMAGE_NAME) .

push: build
	docker push $(IMAGE_NAME)

test:
	uv run pytest -q

# Per-model numbers from the session logs. MEMORY=/path/to/memory, DAYS=7.
report:
	uv run python -m ai_brain.report --memory $(or $(MEMORY),../volumes/ai-brain-memory) $(if $(DAYS),--days $(DAYS))

dev:
	docker run --rm --env-file .env -e DRY_RUN=1 -v $(PWD)/../volumes/ai-brain-memory:/memory $(IMAGE_NAME)
