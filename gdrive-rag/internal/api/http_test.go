package api

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/budget"
	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/store"
	syncpkg "github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/sync"
)

// fakeLooper satisfies looperIface for tests.
type fakeLooper struct {
	mu           sync.Mutex
	reindexCalls []string
	reindexErr   error
	status       syncpkg.Status
}

func (f *fakeLooper) Reindex(_ context.Context, folder string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.reindexCalls = append(f.reindexCalls, folder)
	return f.reindexErr
}
func (f *fakeLooper) Status(_ context.Context) syncpkg.Status { return f.status }

func (f *fakeLooper) ReindexCalls() []string {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]string(nil), f.reindexCalls...)
}

// fakeEmbedder satisfies embedderIface.
type fakeEmbedder struct {
	vec []float32
	err error
}

func (f *fakeEmbedder) EmbedQuery(_ context.Context, _ string) ([]float32, error) {
	return f.vec, f.err
}

// fakeStore satisfies storeIface.
type fakeStore struct {
	results []store.QueryResult
	err     error
	lastOpt store.QueryOptions
}

func (f *fakeStore) Query(_ context.Context, _ []float32, opts store.QueryOptions) ([]store.QueryResult, error) {
	f.lastOpt = opts
	return f.results, f.err
}

func newTestService() (*Service, *fakeLooper, *fakeEmbedder, *fakeStore) {
	l := &fakeLooper{status: syncpkg.Status{
		LastSync:     time.Date(2026, 4, 20, 12, 0, 0, 0, time.UTC),
		ExtractModel: "gemini-2.5-flash-lite",
		EmbedModel:   "gemini-embedding-001",
		EmbedTPMCap:  200_000,
		FlashDailyCap: 800,
	}}
	e := &fakeEmbedder{vec: []float32{0.1, 0.2, 0.3}}
	s := &fakeStore{}
	return &Service{Looper: l, Embedder: e, Store: s}, l, e, s
}

func TestHealthzReturnsOK(t *testing.T) {
	svc, _, _, _ := newTestService()
	srv := httptest.NewServer(svc.Handler())
	defer srv.Close()

	resp, err := http.Get(srv.URL + "/healthz")
	if err != nil {
		t.Fatalf("GET /healthz: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status: got %d want 200", resp.StatusCode)
	}
	body, _ := io.ReadAll(resp.Body)
	if string(body) != "ok" {
		t.Fatalf("body: got %q want %q", string(body), "ok")
	}
}

func TestQueryRejectsEmptyQuery(t *testing.T) {
	svc, _, _, _ := newTestService()
	srv := httptest.NewServer(svc.Handler())
	defer srv.Close()

	resp, err := http.Post(srv.URL+"/query", "application/json",
		strings.NewReader(`{"query":""}`))
	if err != nil {
		t.Fatalf("POST /query: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusBadRequest {
		t.Fatalf("status: got %d want 400", resp.StatusCode)
	}
	var out map[string]string
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if out["error"] == "" {
		t.Fatalf("expected error field, got %v", out)
	}
}

func TestQueryRejectsInvalidJSON(t *testing.T) {
	svc, _, _, _ := newTestService()
	srv := httptest.NewServer(svc.Handler())
	defer srv.Close()

	resp, err := http.Post(srv.URL+"/query", "application/json",
		strings.NewReader(`{not json}`))
	if err != nil {
		t.Fatalf("POST /query: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusBadRequest {
		t.Fatalf("status: got %d want 400", resp.StatusCode)
	}
}

func TestQueryReturnsHits(t *testing.T) {
	svc, _, _, fs := newTestService()
	modified := time.Date(2026, 4, 1, 10, 0, 0, 0, time.UTC)
	fs.results = []store.QueryResult{
		{
			Chunk: store.Chunk{
				FileID:       "abc",
				ChunkIndex:   2,
				Text:         "hello world",
				FileName:     "doc.pdf",
				MimeType:     "application/pdf",
				FolderPath:   "Projects/Alpha",
				ModifiedTime: modified,
				WebViewLink:  "https://drive.google.com/file/d/abc/view",
			},
			Similarity: 0.91,
		},
	}

	srv := httptest.NewServer(svc.Handler())
	defer srv.Close()

	body := `{"query":"hello","top_k":5,"folder_filter":"Projects/"}`
	resp, err := http.Post(srv.URL+"/query", "application/json",
		strings.NewReader(body))
	if err != nil {
		t.Fatalf("POST /query: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status: got %d want 200", resp.StatusCode)
	}
	var hits []QueryHit
	if err := json.NewDecoder(resp.Body).Decode(&hits); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if len(hits) != 1 {
		t.Fatalf("hits: got %d want 1", len(hits))
	}
	h := hits[0]
	if h.Text != "hello world" || h.FileName != "doc.pdf" || h.ChunkIndex != 2 {
		t.Fatalf("hit fields wrong: %+v", h)
	}
	if h.Similarity < 0.9 || h.Similarity > 0.92 {
		t.Fatalf("similarity: got %v", h.Similarity)
	}
	if fs.lastOpt.FolderPrefix != "Projects/" {
		t.Fatalf("FolderPrefix not passed through: %q", fs.lastOpt.FolderPrefix)
	}
	if fs.lastOpt.TopK != 5 {
		t.Fatalf("TopK not passed through: %d", fs.lastOpt.TopK)
	}
}

func TestQueryCapsTopK(t *testing.T) {
	svc, _, _, fs := newTestService()
	srv := httptest.NewServer(svc.Handler())
	defer srv.Close()

	resp, err := http.Post(srv.URL+"/query", "application/json",
		strings.NewReader(`{"query":"x","top_k":500}`))
	if err != nil {
		t.Fatalf("post: %v", err)
	}
	defer resp.Body.Close()
	if fs.lastOpt.TopK != MaxTopK {
		t.Fatalf("TopK: got %d want %d", fs.lastOpt.TopK, MaxTopK)
	}
}

func TestQueryDefaultsTopK(t *testing.T) {
	svc, _, _, fs := newTestService()
	srv := httptest.NewServer(svc.Handler())
	defer srv.Close()

	resp, err := http.Post(srv.URL+"/query", "application/json",
		strings.NewReader(`{"query":"x"}`))
	if err != nil {
		t.Fatalf("post: %v", err)
	}
	defer resp.Body.Close()
	if fs.lastOpt.TopK != DefaultTopK {
		t.Fatalf("TopK: got %d want %d", fs.lastOpt.TopK, DefaultTopK)
	}
}

func TestQueryBudgetExhausted503(t *testing.T) {
	svc, _, e, _ := newTestService()
	e.err = budget.ErrDailyBudgetExhausted

	srv := httptest.NewServer(svc.Handler())
	defer srv.Close()

	resp, err := http.Post(srv.URL+"/query", "application/json",
		strings.NewReader(`{"query":"x"}`))
	if err != nil {
		t.Fatalf("post: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusServiceUnavailable {
		t.Fatalf("status: got %d want 503", resp.StatusCode)
	}
}

func TestQueryEmbedderError500(t *testing.T) {
	svc, _, e, _ := newTestService()
	e.err = errors.New("kaboom")

	srv := httptest.NewServer(svc.Handler())
	defer srv.Close()

	resp, err := http.Post(srv.URL+"/query", "application/json",
		strings.NewReader(`{"query":"x"}`))
	if err != nil {
		t.Fatalf("post: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusInternalServerError {
		t.Fatalf("status: got %d want 500", resp.StatusCode)
	}
}

func TestStatusJSONShape(t *testing.T) {
	svc, _, _, _ := newTestService()
	srv := httptest.NewServer(svc.Handler())
	defer srv.Close()

	resp, err := http.Get(srv.URL + "/status")
	if err != nil {
		t.Fatalf("GET /status: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status: got %d want 200", resp.StatusCode)
	}
	var raw map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&raw); err != nil {
		t.Fatalf("decode: %v", err)
	}
	wantKeys := []string{
		"last_sync", "document_count", "chunk_count", "queue_depth",
		"skipped_count", "embed_tokens_today", "embed_tpm_cap",
		"flash_requests_today", "flash_daily_cap",
		"extract_model", "embed_model",
	}
	for _, k := range wantKeys {
		if _, ok := raw[k]; !ok {
			t.Errorf("status missing key %q (got %v)", k, raw)
		}
	}
	if raw["extract_model"] != "gemini-2.5-flash-lite" {
		t.Errorf("extract_model: got %v", raw["extract_model"])
	}
	if raw["last_sync"] != "2026-04-20T12:00:00Z" {
		t.Errorf("last_sync: got %v", raw["last_sync"])
	}
}

func TestReindexReturns202AndTriggers(t *testing.T) {
	svc, l, _, _ := newTestService()
	srv := httptest.NewServer(svc.Handler())
	defer srv.Close()

	resp, err := http.Post(srv.URL+"/reindex", "application/json",
		bytes.NewReader([]byte(`{"folder":"fid123"}`)))
	if err != nil {
		t.Fatalf("POST /reindex: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusAccepted {
		t.Fatalf("status: got %d want 202", resp.StatusCode)
	}

	// Reindex fires a goroutine — poll briefly for the side effect.
	deadline := time.Now().Add(500 * time.Millisecond)
	for time.Now().Before(deadline) {
		calls := l.ReindexCalls()
		if len(calls) == 1 && calls[0] == "fid123" {
			return
		}
		time.Sleep(5 * time.Millisecond)
	}
	t.Fatalf("looper.Reindex not called with fid123; calls=%v", l.ReindexCalls())
}

func TestReindexEmptyBodyAllowed(t *testing.T) {
	svc, l, _, _ := newTestService()
	srv := httptest.NewServer(svc.Handler())
	defer srv.Close()

	req, _ := http.NewRequest(http.MethodPost, srv.URL+"/reindex", nil)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("POST /reindex: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusAccepted {
		t.Fatalf("status: got %d want 202", resp.StatusCode)
	}

	deadline := time.Now().Add(500 * time.Millisecond)
	for time.Now().Before(deadline) {
		calls := l.ReindexCalls()
		if len(calls) == 1 && calls[0] == "" {
			return
		}
		time.Sleep(5 * time.Millisecond)
	}
	t.Fatalf("looper.Reindex not called; calls=%v", l.ReindexCalls())
}

func TestMethodNotAllowed(t *testing.T) {
	svc, _, _, _ := newTestService()
	srv := httptest.NewServer(svc.Handler())
	defer srv.Close()

	// /query is POST-only — GET should 405.
	resp, err := http.Get(srv.URL + "/query")
	if err != nil {
		t.Fatalf("GET /query: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusMethodNotAllowed {
		t.Fatalf("status: got %d want 405", resp.StatusCode)
	}
}
