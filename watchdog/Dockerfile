# Stage 1: build the Go binary
FROM golang:1.26-bookworm AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY cmd ./cmd
COPY internal ./internal
RUN CGO_ENABLED=0 go build -trimpath -ldflags "-s -w" -o /out/watchdog ./cmd/watchdog

# Stage 2: minimal runtime. Runs as root: it needs read-write access to
# /var/run/docker.sock, which is root:docker 0660 on the host.
FROM gcr.io/distroless/static-debian12
COPY --from=build /out/watchdog /watchdog
ENTRYPOINT ["/watchdog"]
