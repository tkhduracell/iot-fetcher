build-fetcher:
	(cd ./fetcher-core && make build)

push-fetcher: build-fetcher
	(cd ./fetcher-core && make push)

build-proxy:
	(cd ./https-proxy && make build)

push-proxy: build-proxy
	(cd ./https-proxy && make push)

build-pool-pump-planner:
	(cd ./pool-pump-planner && make build)

push-pool-pump-planner: build-pool-pump-planner
	(cd ./pool-pump-planner && make push)

build-sigenergy-bridge:
	(cd ./sigenergy-bridge && make build)

push-sigenergy-bridge: build-sigenergy-bridge
	(cd ./sigenergy-bridge && make push)

build-gdrive-rag:
	(cd ./gdrive-rag && make build)

push-gdrive-rag: build-gdrive-rag
	(cd ./gdrive-rag && make push)

build-ai-brain:
	(cd ./ai-brain && make build)

push-ai-brain: build-ai-brain
	(cd ./ai-brain && make push)

login:
	balena login -H --token "$$(sed -n 's/^BALENA_TOKEN=//p' .env)"

deploy: push-fetcher push-proxy login
	balena push iot-hub

.PHONY: build-fetcher push-fetcher deploy build-proxy push-proxy build-pool-pump-planner push-pool-pump-planner build-sigenergy-bridge push-sigenergy-bridge build-gdrive-rag push-gdrive-rag build-ai-brain push-ai-brain login run-proxy
