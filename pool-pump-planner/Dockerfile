# Stage 1: build the Go binary
FROM golang:1.26-bookworm AS build
WORKDIR /src
COPY go.mod ./
RUN go mod download
COPY *.go ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags "-s -w" -o /out/pool-pump-planner .

# Stage 2: runtime with CBC
FROM debian:bookworm-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates coinor-cbc tzdata \
    && apt-get clean && rm -rf /var/lib/apt/lists/*
COPY --from=build /out/pool-pump-planner /usr/local/bin/pool-pump-planner
ENTRYPOINT ["/usr/local/bin/pool-pump-planner"]
