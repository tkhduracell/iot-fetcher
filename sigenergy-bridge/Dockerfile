# Stage 1: build the Go binary
FROM golang:1.26-bookworm AS build
# Build context is the repo root (the shared influxmetrics module is a sibling,
# referenced by a replace directive in go.mod).
WORKDIR /src
COPY influxmetrics ./influxmetrics
WORKDIR /src/sigenergy-bridge
COPY sigenergy-bridge/go.mod sigenergy-bridge/go.sum ./
RUN go mod download
COPY sigenergy-bridge/cmd ./cmd
COPY sigenergy-bridge/internal ./internal
RUN CGO_ENABLED=0 go build -trimpath -ldflags "-s -w" -o /out/sigenergy-bridge ./cmd/bridge

# Stage 2: minimal runtime
FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/sigenergy-bridge /sigenergy-bridge
USER nonroot:nonroot
ENTRYPOINT ["/sigenergy-bridge"]
