build: 
	docker build -t europe-docker.pkg.dev/filiplindqvist-com-ea66d/images/iot-fetcher:latest .

push: build
	gcloud auth configure-docker europe-docker.pkg.dev
	docker push europe-docker.pkg.dev/filiplindqvist-com-ea66d/images/iot-fetcher:latest

# Requires ZeroSSL EAB vars at runtime: ZEROSSL_EAB_KID and ZEROSSL_EAB_HMAC in .env
build-proxy:
	docker build \
		-t europe-docker.pkg.dev/filiplindqvist-com-ea66d/images/influxdb-proxy:latest \
		./influx-proxy

push-proxy: build-proxy
	gcloud auth configure-docker europe-docker.pkg.dev
	docker push europe-docker.pkg.dev/filiplindqvist-com-ea66d/images/influxdb-proxy:latest

run-proxy: build-proxy
	@echo "\nStarting proxy on port 8443 and 8080...\n"
	docker run --rm -p 8443:443 -p 8080:80 --env-file influx-proxy/.env \
		europe-docker.pkg.dev/filiplindqvist-com-ea66d/images/influxdb-proxy:latest

run-webui:
	@echo "\nStarting webui web on port 8080...\n"
	(cd webui && uvx --from . web.py)

# Alternative: use uv to run with dependencies installed
run-webui-dev:
	@echo "\nStarting webui in dev mode with uv...\n"
	(cd webui && uv run python web.py)

login:
	balena login -H --token "$$(sed -n 's/^BALENA_TOKEN=//p' .env)"

deploy: push push-proxy login
	balena push iot-hub

run:
	docker build . -t iot-fetcher:latest && docker run --rm -p 8080:8080 --env-file .env iot-fetcher:latest -- $(MODULE)

dev:
	docker build . -t iot-fetcher:latest-dev --build-arg PYDEBUGGER=1 && docker run -e PYDEBUGGER=1 -p 5678:5678 --rm -p 8080:8080 --env-file .env iot-fetcher:latest-dev

# Sync Python dependencies using uv
sync-deps:
	@echo "\nSyncing main backend dependencies...\n"
	cd python && uv pip compile pyproject.toml -o requirements.txt
	@echo "\nSyncing webui dependencies...\n"
	cd webui && uv pip compile pyproject.toml -o requirements.txt

# Install uv (if not already installed)
install-uv:
	@command -v uv >/dev/null 2>&1 || { echo "Installing uv..."; curl -LsSf https://astral.sh/uv/install.sh | sh; }

.PHONY: build push deploy run dev build-proxy push-proxy login run-proxy run-webui run-webui-dev sync-deps install-uv
