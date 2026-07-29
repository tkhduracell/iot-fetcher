# Stage 1: runtime deps (cached independently of Go sources — BuildKit can
# pull this in parallel with the Go build stage below)
FROM debian:bookworm-slim AS runtime-deps
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates coinor-cbc tzdata \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Stage 2: build the Go binary
FROM golang:1.26-bookworm AS build
WORKDIR /src
COPY go.mod ./
RUN go mod download
COPY *.go ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags "-s -w" -o /out/pool-pump-planner .

# Stage 3: final image
FROM runtime-deps
COPY --from=build /out/pool-pump-planner /usr/local/bin/pool-pump-planner
ENTRYPOINT ["/usr/local/bin/pool-pump-planner"]
