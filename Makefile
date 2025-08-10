build: 
	docker build -t europe-docker.pkg.dev/filiplindqvist-com-ea66d/images/iot-fetcher:latest .

push: build
	gcloud auth configure-docker europe-docker.pkg.dev
	docker push europe-docker.pkg.dev/filiplindqvist-com-ea66d/images/iot-fetcher:latest

# Build proxy image with build args from .env (PROXY_DOMAIN and LETSENCRYPT_EMAIL)
build-proxy:
	$(eval DOMAIN := $(shell grep -E '^PROXY_DOMAIN=' .env | cut -d'=' -f2-))
	$(eval EMAIL := $(shell grep -E '^LETSENCRYPT_EMAIL=' .env | cut -d'=' -f2-))
	docker build \
		--build-arg DOMAIN=$(DOMAIN) \
		--build-arg EMAIL=$(EMAIL) \
		-t europe-docker.pkg.dev/filiplindqvist-com-ea66d/images/influxdb-proxy:latest \
		./influx-proxy

push-proxy: build-proxy
	gcloud auth configure-docker europe-docker.pkg.dev
	docker push europe-docker.pkg.dev/filiplindqvist-com-ea66d/images/influxdb-proxy:latest

deploy: push push-proxy
	balena push iot-hub

run:
	docker build . -t iot-fetcher:latest && docker run --rm -p 8080:8080 --env-file .env iot-fetcher:latest -- $(MODULE)

dev:
	docker build . -t iot-fetcher:latest-dev --build-arg PYDEBUGGER=1 && docker run -e PYDEBUGGER=1 -p 5678:5678 --rm -p 8080:8080 --env-file .env iot-fetcher:latest-dev

.PHONY: build push deploy run dev build-proxy push-proxy
