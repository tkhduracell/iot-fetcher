package mcp

import (
	"log/slog"
	"net/http/httptest"
	"testing"

	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/api"
)

func TestNewServerBuildsHandler(t *testing.T) {
	// An api.Service with nil dependencies is fine for this smoke test; we
	// only want to confirm the server builds and mounts as an http.Handler.
	svc := &api.Service{Logger: slog.Default()}
	h, err := NewServer(svc, slog.Default())
	if err != nil {
		t.Fatalf("NewServer: %v", err)
	}
	if h == nil {
		t.Fatal("NewServer returned nil handler")
	}

	// Boot the handler through httptest to confirm it responds without panicking.
	// The Streamable HTTP transport returns 4xx on a bare GET since it
	// expects POST JSON-RPC, but it should not 5xx or hang.
	srv := httptest.NewServer(h)
	defer srv.Close()

	// We're not asserting a specific status; we're asserting the handler
	// wires up and responds at all.
	resp, err := srv.Client().Get(srv.URL)
	if err != nil {
		t.Fatalf("GET /: %v", err)
	}
	_ = resp.Body.Close()
	if resp.StatusCode >= 500 {
		t.Fatalf("status: got %d (wanted <500)", resp.StatusCode)
	}
}

func TestNewServerRejectsNilService(t *testing.T) {
	if _, err := NewServer(nil, slog.Default()); err == nil {
		t.Fatal("NewServer(nil, ...) should error")
	}
}
