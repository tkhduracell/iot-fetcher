# Stage 1: build the Go binary
FROM golang:1.26-bookworm AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY cmd ./cmd
COPY internal ./internal
RUN CGO_ENABLED=0 go build -trimpath -ldflags "-s -w" -o /out/sigenergy-bridge ./cmd/bridge

# Stage 2: minimal runtime
FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/sigenergy-bridge /sigenergy-bridge
USER nonroot:nonroot
ENTRYPOINT ["/sigenergy-bridge"]
