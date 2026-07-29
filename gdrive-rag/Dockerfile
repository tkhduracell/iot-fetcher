# Stage 1: build the Go binary
FROM golang:1.26-bookworm AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY cmd ./cmd
COPY internal ./internal
RUN CGO_ENABLED=0 go build -trimpath -ldflags "-s -w" -o /out/gdrive-rag ./cmd/rag

# Stage 2: minimal runtime
FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/gdrive-rag /gdrive-rag
USER nonroot:nonroot
ENTRYPOINT ["/gdrive-rag"]
