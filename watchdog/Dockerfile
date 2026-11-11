# Stage 1: build the Go binary
FROM golang:1.26-bookworm AS build
# Build context is the repo root (the shared influxmetrics module is a sibling,
# referenced by a replace directive in go.mod).
WORKDIR /src
COPY influxmetrics ./influxmetrics
WORKDIR /src/watchdog
COPY watchdog/go.mod watchdog/go.sum ./
RUN go mod download
COPY watchdog/cmd ./cmd
COPY watchdog/internal ./internal
RUN CGO_ENABLED=0 go build -trimpath -ldflags "-s -w" -o /out/watchdog ./cmd/watchdog

# Stage 2: minimal runtime. Runs as root: it needs read-write access to
# /var/run/docker.sock, which is root:docker 0660 on the host.
FROM gcr.io/distroless/static-debian12
COPY --from=build /out/watchdog /watchdog
ENTRYPOINT ["/watchdog"]
