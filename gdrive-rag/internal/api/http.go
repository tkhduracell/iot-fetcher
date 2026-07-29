// Package api exposes the HTTP surface: /healthz, /status, /query, and
// /reindex. A separate /mcp handler is composed by main.go from the internal/mcp
// package and mounted alongside this one.
package api

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"time"

	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/budget"
	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/embed"
	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/store"
	syncpkg "github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/sync"
)

// DefaultTopK is used when QueryRequest.TopK is zero.
const DefaultTopK = 10

// MaxTopK caps QueryRequest.TopK to protect the store from runaway requests.
const MaxTopK = 50

// maxRequestBody is the largest JSON body we'll accept on any endpoint.
const maxRequestBody = 1 << 20 // 1 MiB

// QueryRequest is the JSON payload for POST /query and the MCP search_drive tool.
type QueryRequest struct {
	Query        string `json:"query"`
	TopK         int    `json:"top_k,omitempty"`
	FolderFilter string `json:"folder_filter,omitempty"`
	MimeFilter   string `json:"mime_filter,omitempty"`
}

// QueryHit is one similarity-ranked chunk returned to the caller.
type QueryHit struct {
	Text         string    `json:"text"`
	FileName     string    `json:"file_name"`
	WebViewLink  string    `json:"web_view_link"`
	MimeType     string    `json:"mime_type"`
	ModifiedTime time.Time `json:"modified_time"`
	ChunkIndex   int       `json:"chunk_index"`
	Similarity   float32   `json:"similarity"`
}

// ReindexRequest is the JSON payload for POST /reindex and the MCP reindex tool.
type ReindexRequest struct {
	Folder string `json:"folder,omitempty"`
}

// StatusResponse mirrors sync.Status but with json tags and RFC3339 timestamps
// so the wire format is stable independent of the internal struct.
type StatusResponse struct {
	LastSync           string `json:"last_sync"`
	DocumentCount      int    `json:"document_count"`
	ChunkCount         int    `json:"chunk_count"`
	QueueDepth         int    `json:"queue_depth"`
	SkippedCount       int    `json:"skipped_count"`
	EmbedTokensToday   int64  `json:"embed_tokens_today"`
	EmbedTPMCap        int64  `json:"embed_tpm_cap"`
	FlashRequestsToday int64  `json:"flash_requests_today"`
	FlashDailyCap      int64  `json:"flash_daily_cap"`
	ExtractModel       string `json:"extract_model"`
	EmbedModel         string `json:"embed_model"`
}

// looperIface is the subset of *sync.Looper that Service needs. Defined as an
// interface so tests can provide a fake without wiring the full sync stack.
type looperIface interface {
	Reindex(ctx context.Context, folderID string) error
	Status(ctx context.Context) syncpkg.Status
}

// embedderIface is the subset of *embed.Client that Service needs.
type embedderIface interface {
	EmbedQuery(ctx context.Context, text string) ([]float32, error)
}

// storeIface is the subset of *store.Store that Service needs.
type storeIface interface {
	Query(ctx context.Context, embedding []float32, opts store.QueryOptions) ([]store.QueryResult, error)
}

// Service wires the HTTP endpoints (and, via a sibling package, the MCP tools)
// to the running Looper and the embedder/store pair used by the search path.
type Service struct {
	Looper   looperIface
	Embedder embedderIface
	Store    storeIface
	Logger   *slog.Logger
}

// NewService constructs a Service backed by the concrete types used in
// production. Use zero-value construction (&Service{...}) when swapping fakes
// in tests.
func NewService(looper *syncpkg.Looper, embedder *embed.Client, st *store.Store, logger *slog.Logger) *Service {
	if logger == nil {
		logger = slog.Default()
	}
	return &Service{
		Looper:   looper,
		Embedder: embedder,
		Store:    st,
		Logger:   logger,
	}
}

// Query performs a similarity search. It embeds the query text as a
// RETRIEVAL_QUERY and returns at most TopK hits (default 10, max 50).
func (s *Service) Query(ctx context.Context, req QueryRequest) ([]QueryHit, error) {
	if s == nil {
		return nil, errors.New("api: nil service")
	}
	q := req.Query
	if q == "" {
		return nil, errBadRequest("query is required")
	}
	topK := req.TopK
	if topK <= 0 {
		topK = DefaultTopK
	}
	if topK > MaxTopK {
		topK = MaxTopK
	}

	if s.Embedder == nil {
		return nil, errors.New("api: embedder not configured")
	}
	if s.Store == nil {
		return nil, errors.New("api: store not configured")
	}

	vec, err := s.Embedder.EmbedQuery(ctx, q)
	if err != nil {
		return nil, fmt.Errorf("api: embed query: %w", err)
	}
	results, err := s.Store.Query(ctx, vec, store.QueryOptions{
		TopK:         topK,
		FolderPrefix: req.FolderFilter,
		MimeFilter:   req.MimeFilter,
	})
	if err != nil {
		return nil, fmt.Errorf("api: store query: %w", err)
	}
	out := make([]QueryHit, 0, len(results))
	for _, r := range results {
		out = append(out, QueryHit{
			Text:         r.Chunk.Text,
			FileName:     r.Chunk.FileName,
			WebViewLink:  r.Chunk.WebViewLink,
			MimeType:     r.Chunk.MimeType,
			ModifiedTime: r.Chunk.ModifiedTime,
			ChunkIndex:   r.Chunk.ChunkIndex,
			Similarity:   r.Similarity,
		})
	}
	return out, nil
}

// Reindex asks the Looper to enumerate the given folder (or every whitelisted
// folder when req.Folder is empty) and enqueue every file for re-indexing.
// Intended to be launched in a goroutine by the HTTP handler so the caller
// doesn't block on the full crawl.
func (s *Service) Reindex(ctx context.Context, req ReindexRequest) error {
	if s == nil || s.Looper == nil {
		return errors.New("api: looper not configured")
	}
	return s.Looper.Reindex(ctx, req.Folder)
}

// Status returns the current Looper snapshot in wire-ready form.
func (s *Service) Status(ctx context.Context) StatusResponse {
	if s == nil || s.Looper == nil {
		return StatusResponse{}
	}
	st := s.Looper.Status(ctx)
	var lastSync string
	if !st.LastSync.IsZero() {
		lastSync = st.LastSync.UTC().Format(time.RFC3339)
	}
	return StatusResponse{
		LastSync:           lastSync,
		DocumentCount:      st.DocumentCount,
		ChunkCount:         st.ChunkCount,
		QueueDepth:         st.QueueDepth,
		SkippedCount:       st.SkippedCount,
		EmbedTokensToday:   st.EmbedTokensToday,
		EmbedTPMCap:        st.EmbedTPMCap,
		FlashRequestsToday: st.FlashRequestsToday,
		FlashDailyCap:      st.FlashDailyCap,
		ExtractModel:       st.ExtractModel,
		EmbedModel:         st.EmbedModel,
	}
}

// Handler returns the HTTP mux wiring the service's endpoints. The caller is
// responsible for mounting the MCP handler on /mcp separately.
func (s *Service) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", s.handleHealth)
	mux.HandleFunc("GET /status", s.handleStatus)
	mux.HandleFunc("POST /query", s.handleQuery)
	mux.HandleFunc("POST /reindex", s.handleReindex)
	return mux
}

func (s *Service) handleHealth(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "text/plain; charset=utf-8")
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write([]byte("ok"))
}

func (s *Service) handleStatus(w http.ResponseWriter, r *http.Request) {
	resp := s.Status(r.Context())
	writeJSON(w, http.StatusOK, resp)
}

func (s *Service) handleQuery(w http.ResponseWriter, r *http.Request) {
	var req QueryRequest
	if err := decodeJSON(w, r, &req); err != nil {
		writeError(w, http.StatusBadRequest, err.Error())
		return
	}
	hits, err := s.Query(r.Context(), req)
	if err != nil {
		status, msg := classifyError(err)
		writeError(w, status, msg)
		return
	}
	writeJSON(w, http.StatusOK, hits)
}

func (s *Service) handleReindex(w http.ResponseWriter, r *http.Request) {
	var req ReindexRequest
	// Reindex takes an optional body; accept empty body as "all folders".
	if r.ContentLength != 0 {
		if err := decodeJSON(w, r, &req); err != nil {
			writeError(w, http.StatusBadRequest, err.Error())
			return
		}
	}

	logger := s.logger()

	// Fire-and-forget: the full crawl may take a long time. We run it on a
	// background context so the HTTP caller's context cancellation doesn't
	// abort the crawl.
	go func(folder string) {
		bgCtx := context.Background()
		if err := s.Reindex(bgCtx, ReindexRequest{Folder: folder}); err != nil {
			logger.Error("api: reindex failed", "folder", folder, "err", err)
			return
		}
		logger.Info("api: reindex complete", "folder", folder)
	}(req.Folder)

	writeJSON(w, http.StatusAccepted, map[string]string{
		"status": "accepted",
		"folder": req.Folder,
	})
}

// logger returns the service logger or slog.Default() if unset.
func (s *Service) logger() *slog.Logger {
	if s != nil && s.Logger != nil {
		return s.Logger
	}
	return slog.Default()
}

// --- helpers ----------------------------------------------------------------

// badRequestError is a typed marker so classifyError can return 400.
type badRequestError struct{ msg string }

func (e *badRequestError) Error() string { return e.msg }

func errBadRequest(msg string) error { return &badRequestError{msg: msg} }

// classifyError maps a Service error to an HTTP status + user-facing message.
func classifyError(err error) (int, string) {
	var bad *badRequestError
	if errors.As(err, &bad) {
		return http.StatusBadRequest, bad.msg
	}
	if errors.Is(err, budget.ErrDailyBudgetExhausted) {
		return http.StatusServiceUnavailable, "daily budget exhausted; try again after the next 00:05 Pacific reset"
	}
	return http.StatusInternalServerError, err.Error()
}

// decodeJSON enforces a 1 MiB body cap and rejects unknown fields so typos
// fail fast instead of being silently ignored. The ResponseWriter is passed
// to MaxBytesReader so an oversized body results in a clean 413 with the
// connection closed, rather than a bare EOF.
func decodeJSON(w http.ResponseWriter, r *http.Request, dst any) error {
	r.Body = http.MaxBytesReader(w, r.Body, maxRequestBody)
	dec := json.NewDecoder(r.Body)
	dec.DisallowUnknownFields()
	if err := dec.Decode(dst); err != nil {
		return fmt.Errorf("invalid JSON: %w", err)
	}
	return nil
}

// writeJSON serializes v and writes it with the given status code. Marshal
// errors are logged but not surfaced — the response body is empty in that case.
func writeJSON(w http.ResponseWriter, status int, v any) {
	body, err := json.Marshal(v)
	if err != nil {
		slog.Default().Error("api: json marshal failed", "err", err)
		http.Error(w, `{"error":"internal error"}`, http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	_, _ = w.Write(body)
}

// writeError writes a JSON error envelope: {"error":"..."}.
func writeError(w http.ResponseWriter, status int, msg string) {
	writeJSON(w, status, map[string]string{"error": msg})
}
