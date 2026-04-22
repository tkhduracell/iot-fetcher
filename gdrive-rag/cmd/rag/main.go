// Command rag is the gdrive-rag entry point: it wires the internal packages
// (state, queue, store, drive, embed, extract, sync) into a running service
// exposing an HTTP API and an MCP server for Claude Code.
package main

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/api"
	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/budget"
	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/drive"
	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/embed"
	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/extract"
	mcpserver "github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/mcp"
	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/queue"
	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/state"
	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/store"
	syncpkg "github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/sync"
)

func main() {
	if err := run(); err != nil {
		slog.Error("gdrive-rag: fatal", "err", err)
		os.Exit(1)
	}
}

func run() error {
	cfg, err := LoadConfig()
	if err != nil {
		return fmt.Errorf("load config: %w", err)
	}

	logger := newLogger(cfg.LogLevel)
	slog.SetDefault(logger)

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	// Ensure the data directory exists so state/queue/store can write.
	if err := os.MkdirAll(cfg.DataDir, 0o755); err != nil {
		return fmt.Errorf("mkdir %q: %w", cfg.DataDir, err)
	}

	// State.
	statePath := filepath.Join(cfg.DataDir, "state.json")
	st, err := state.Load(statePath)
	if err != nil {
		return fmt.Errorf("load state: %w", err)
	}

	// Queue.
	queuePath := filepath.Join(cfg.DataDir, "ingest_queue.json")
	q, err := queue.Open(queuePath)
	if err != nil {
		return fmt.Errorf("open queue: %w", err)
	}

	// Store (chromem-go).
	storeDir := filepath.Join(cfg.DataDir, "chromem")
	if err := os.MkdirAll(storeDir, 0o755); err != nil {
		return fmt.Errorf("mkdir store: %w", err)
	}
	str, err := store.Open(storeDir)
	if err != nil {
		return fmt.Errorf("open store: %w", err)
	}
	defer func() { _ = str.Close() }()

	// Drive.
	drv, err := drive.NewClient(ctx, []byte(cfg.ServiceAccountJSON))
	if err != nil {
		return fmt.Errorf("drive client: %w", err)
	}

	// Budget limiters.
	//
	// Embedding TPM is a token-per-second bucket with burst = the full minute
	// cap. DailyCounter for embeddings is observability-only (cap=0 → never
	// trips), because the Gemini embeddings API is TPM-gated rather than RPD-
	// gated.
	embedTPM := budget.NewRateLimiter(float64(cfg.EmbedTPMCap)/60.0, float64(cfg.EmbedTPMCap))

	// Flash RPM is a request-per-second bucket, burst = the full minute cap.
	flashRPM := budget.NewRateLimiter(float64(cfg.FlashRPMCap)/60.0, float64(cfg.FlashRPMCap))
	flashDaily := &budget.DailyCounter{
		Cap:       cfg.FlashDailyCap,
		Current:   func() int64 { return st.Snapshot().FlashRequestsToday },
		Increment: func(int64) { st.AddFlashRequest() },
	}

	// Embedder.
	embClient, err := embed.NewClient(ctx, embed.Config{
		APIKey:       cfg.GeminiAPIKey,
		Model:        cfg.EmbedModel,
		BatchSize:    cfg.EmbedBatchSize,
		TPMLimiter:   embedTPM,
		RecordTokens: func(n int64) { st.AddEmbedTokens(n) },
	})
	if err != nil {
		return fmt.Errorf("embed client: %w", err)
	}
	defer func() { _ = embClient.Close() }()

	// Flash + Extractor.
	flashClient, err := extract.NewFlashClient(ctx, extract.FlashConfig{
		APIKey:       cfg.GeminiAPIKey,
		Model:        cfg.ExtractModel,
		RPMLimiter:   flashRPM,
		DailyCounter: flashDaily,
	})
	if err != nil {
		return fmt.Errorf("flash client: %w", err)
	}
	defer func() { _ = flashClient.Close() }()

	router := extract.NewRouter(extract.Config{
		Flash:                       flashClient,
		PDFTextLayerMinCharsPerPage: cfg.PDFMinCharsPerPage,
		PDFMaxPagesPerCall:          cfg.PDFMaxPagesPerCall,
		PDFMaxPages:                 cfg.MaxPDFPages,
		SkipImages:                  cfg.SkipImages,
	})

	// Looper.
	looper, err := syncpkg.NewLooper(syncpkg.Config{
		State:              st,
		StatePath:          statePath,
		Queue:              q,
		Store:              str,
		Drive:              drv,
		Extractor:          router,
		Embedder:           embClient,
		WhitelistedFolders: cfg.WhitelistedFolders,
		Interval:           cfg.SyncInterval,
		ChunkTokens:        cfg.ChunkTokens,
		ChunkOverlap:       cfg.ChunkOverlap,
		MaxFileSizeMB:      cfg.MaxFileSizeMB,
		ExtractModel:       cfg.ExtractModel,
		EmbedModel:         cfg.EmbedModel,
		EmbedTPMCap:        cfg.EmbedTPMCap,
		FlashDailyCap:      cfg.FlashDailyCap,
		Logger:             logger,
	})
	if err != nil {
		return fmt.Errorf("sync looper: %w", err)
	}

	// Service + MCP handler + mux.
	svc := api.NewService(looper, embClient, str, logger)

	mcpHandler, err := mcpserver.NewServer(svc, logger)
	if err != nil {
		return fmt.Errorf("mcp server: %w", err)
	}

	mux := http.NewServeMux()
	mux.Handle("/", svc.Handler())
	// Mount the MCP handler on both /mcp and /mcp/ so clients that append a
	// trailing slash hit it too. The StreamableHTTPServer matches the root
	// of whatever prefix it's mounted under.
	mux.Handle("/mcp", mcpHandler)
	mux.Handle("/mcp/", http.StripPrefix("/mcp", mcpHandler))

	httpSrv := &http.Server{
		Addr:              cfg.ListenAddr,
		Handler:           mux,
		ReadHeaderTimeout: 10 * time.Second,
	}

	// Run looper + HTTP server; shut down both on ctx cancel.
	var wg sync.WaitGroup
	errCh := make(chan error, 3)

	wg.Add(1)
	go func() {
		defer wg.Done()
		logger.Info("gdrive-rag: looper starting",
			"whitelist", cfg.WhitelistedFolders,
			"interval", cfg.SyncInterval,
		)
		if err := looper.Run(ctx); err != nil &&
			!errors.Is(err, context.Canceled) &&
			!errors.Is(err, context.DeadlineExceeded) {
			errCh <- fmt.Errorf("looper: %w", err)
			stop() // Tear down the rest of the program if the looper dies.
		}
	}()

	wg.Add(1)
	go func() {
		defer wg.Done()
		logger.Info("gdrive-rag: http server listening", "addr", cfg.ListenAddr)
		if err := httpSrv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			errCh <- fmt.Errorf("http: %w", err)
			stop()
		}
	}()

	wg.Add(1)
	go func() {
		defer wg.Done()
		<-ctx.Done()
		logger.Info("gdrive-rag: shutting down http server")
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		if err := httpSrv.Shutdown(shutdownCtx); err != nil {
			logger.Error("gdrive-rag: http shutdown failed", "err", err)
		}
	}()

	wg.Wait()
	close(errCh)

	// Return the first non-nil error, if any. Context cancellation is not
	// treated as an error at this level.
	for err := range errCh {
		if err != nil {
			return err
		}
	}
	return nil
}

// newLogger builds a slog JSON logger honouring the RAG_LOG_LEVEL setting.
func newLogger(lvl string) *slog.Logger {
	var level slog.Level
	switch strings.ToLower(strings.TrimSpace(lvl)) {
	case "debug":
		level = slog.LevelDebug
	case "warn", "warning":
		level = slog.LevelWarn
	case "error":
		level = slog.LevelError
	default:
		level = slog.LevelInfo
	}
	return slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: level}))
}
