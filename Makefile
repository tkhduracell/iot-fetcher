build-fetcher: 
	(cd ./fetcher-code && make build)

push-fetcher: build-fetcher
	(cd ./fetcher-code && make push)

build-proxy:
	(cd ./https-proxy && make build)

push-proxy: build-proxy
	(cd ./https-proxy && make push)

login:
	balena login -H --token "$$(sed -n 's/^BALENA_TOKEN=//p' .env)"

deploy: push-fetcher push-proxy login
	balena push iot-hub

.PHONY: build-fetcher push-fetcher deploy build-proxy push-proxy login run-proxy
