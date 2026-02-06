# Requires ZeroSSL EAB vars at runtime: ZEROSSL_EAB_KID and ZEROSSL_EAB_HMAC in .env
build:
	docker build \
		-t europe-docker.pkg.dev/filiplindqvist-com-ea66d/images/https-proxy:latest \
		./https-proxy

push: build
	gcloud auth configure-docker europe-docker.pkg.dev
	docker push europe-docker.pkg.dev/filiplindqvist-com-ea66d/images/https-proxy:latest

run: build
	@echo "\nStarting proxy on port 8443 and 8080...\n"
	docker run --rm -p 8443:443 -p 8080:80 --env-file .env \
		europe-docker.pkg.dev/filiplindqvist-com-ea66d/images/https-proxy:latest
