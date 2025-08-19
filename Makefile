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
	(cd  webui && source venv/bin/activate && python web.py)

deploy: push push-proxy
	balena push iot-hub

run:
	docker build . -t iot-fetcher:latest && docker run --rm -p 8080:8080 --env-file .env iot-fetcher:latest -- $(MODULE)

dev:
	docker build . -t iot-fetcher:latest-dev --build-arg PYDEBUGGER=1 && docker run -e PYDEBUGGER=1 -p 5678:5678 --rm -p 8080:8080 --env-file .env iot-fetcher:latest-dev

.PHONY: build push deploy run dev build-proxy push-proxy
