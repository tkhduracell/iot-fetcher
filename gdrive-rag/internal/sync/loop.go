package sync

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"time"

	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/budget"
	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/drive"
	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/embed"
	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/extract"
	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/queue"
	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/state"
	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/store"
)

// driveClient is the subset of *drive.Client used by the sync loop. Extracting
// it as an interface lets tests inject a fake without dialing Google.
type driveClient interface {
	Export(ctx context.Context, fileID, exportMime string) ([]byte, error)
	Download(ctx context.Context, fileID string) ([]byte, error)
	GetStartPageToken(ctx context.Context) (string, error)
	ListChanges(ctx context.Context, pageToken string) ([]drive.Change, string, error)
	IsInWhitelist(ctx context.Context, fileID string, whitelisted []string) (bool, error)
	AncestryPath(ctx context.Context, fileID string, whitelisted []string) (string, error)
	ListFolder(ctx context.Context, folderID string, yield func(*drive.File) error) error
}

// extractor is the subset of *extract.Router used by the sync loop.
type extractor interface {
	Extract(ctx context.Context, mimeType, fileHint string, body []byte) (string, error)
}

// embedder is the subset of *embed.Client used by the sync loop. EmbedQuery is
// not used by the indexing pipeline but is kept separate so callers can still
// import the real client for the search path.
type embedder interface {
	EmbedBatch(ctx context.Context, texts []string) ([][]float32, error)
}

// Config wires the sync loop's dependencies. All pointer/interface fields are
// required unless noted otherwise.
type Config struct {
	State     *state.State
	StatePath string
	Queue     *queue.Queue
	Store     *store.Store
	Drive     *drive.Client
	Extractor *extract.Router
	Embedder  *embed.Client

	WhitelistedFolders []string

	Interval      time.Duration
	ChunkTokens   int
	ChunkOverlap  int
	MaxFileSizeMB int

	ExtractModel string // reported via /status
	EmbedModel   string

	// Optional budget caps reported via /status.
	EmbedTPMCap   int64
	FlashDailyCap int64

	// Logger defaults to slog.Default when nil.
	Logger *slog.Logger
}

// Looper is the running sync orchestrator. Build with NewLooper; start with Run.
type Looper struct {
	state     *state.State
	statePath string
	queue     *queue.Queue
	store     *store.Store
	drive     driveClient
	extract   extractor
	embed     embedder

	whitelisted []string

	interval      time.Duration
	chunkTokens   int
	chunkOverlap  int
	maxFileSizeMB int

	extractModel  string
	embedModel    string
	embedTPMCap   int64
	flashDailyCap int64

	logger *slog.Logger

	// now is the clock used for budget-reset sleeps. Tests override this.
	now func() time.Time
	// sleep is the sleep primitive used when the budget is exhausted. Tests
	// override this.
	sleep func(context.Context, time.Duration) error
}

// NewLooper validates cfg and returns a ready-to-run Looper.
func NewLooper(cfg Config) (*Looper, error) {
	if cfg.State == nil {
		return nil, errors.New("sync: Config.State is required")
	}
	if cfg.StatePath == "" {
		return nil, errors.New("sync: Config.StatePath is required")
	}
	if cfg.Queue == nil {
		return nil, errors.New("sync: Config.Queue is required")
	}
	if cfg.Store == nil {
		return nil, errors.New("sync: Config.Store is required")
	}
	if cfg.Drive == nil {
		return nil, errors.New("sync: Config.Drive is required")
	}
	if cfg.Extractor == nil {
		return nil, errors.New("sync: Config.Extractor is required")
	}
	if cfg.Embedder == nil {
		return nil, errors.New("sync: Config.Embedder is required")
	}
	if cfg.Interval <= 0 {
		return nil, errors.New("sync: Config.Interval must be > 0")
	}
	if cfg.ChunkTokens <= 0 {
		return nil, errors.New("sync: Config.ChunkTokens must be > 0")
	}
	if cfg.ChunkOverlap < 0 {
		return nil, errors.New("sync: Config.ChunkOverlap must be >= 0")
	}
	if cfg.MaxFileSizeMB <= 0 {
		return nil, errors.New("sync: Config.MaxFileSizeMB must be > 0")
	}

	return newLooperWithDeps(cfg, cfg.Drive, cfg.Extractor, cfg.Embedder), nil
}

// newLooperWithDeps is the shared constructor used by NewLooper and the
// test-only injecting constructor. It assumes cfg has already been validated
// (for the pointer fields, at least) by the caller — Drive/Extractor/Embedder
// may be nil on the Config as long as valid interface values are passed.
func newLooperWithDeps(cfg Config, d driveClient, x extractor, e embedder) *Looper {
	logger := cfg.Logger
	if logger == nil {
		logger = slog.Default()
	}
	return &Looper{
		state:         cfg.State,
		statePath:     cfg.StatePath,
		queue:         cfg.Queue,
		store:         cfg.Store,
		drive:         d,
		extract:       x,
		embed:         e,
		whitelisted:   append([]string(nil), cfg.WhitelistedFolders...),
		interval:      cfg.Interval,
		chunkTokens:   cfg.ChunkTokens,
		chunkOverlap:  cfg.ChunkOverlap,
		maxFileSizeMB: cfg.MaxFileSizeMB,
		extractModel:  cfg.ExtractModel,
		embedModel:    cfg.EmbedModel,
		embedTPMCap:   cfg.EmbedTPMCap,
		flashDailyCap: cfg.FlashDailyCap,
		logger:        logger,
		now:           time.Now,
		sleep:         contextSleep,
	}
}

// contextSleep blocks for d or until ctx is cancelled.
func contextSleep(ctx context.Context, d time.Duration) error {
	if d <= 0 {
		return nil
	}
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-t.C:
		return nil
	}
}

// Run blocks until ctx is cancelled. It bootstraps the pageToken if empty,
// runs the initial backfill if state.InitialSyncComplete is false, then ticks
// at Interval. On ErrDailyBudgetExhausted it persists state and sleeps until
// 00:05 Pacific before resuming.
func (l *Looper) Run(ctx context.Context) error {
	// Bootstrap the pageToken up front so the first tick's ListChanges doesn't
	// fail. We persist it immediately so a crash between bootstrap and the
	// first successful tick doesn't re-bootstrap (which would lose history).
	if l.state.Snapshot().PageToken == "" {
		tok, err := l.drive.GetStartPageToken(ctx)
		if err != nil {
			return fmt.Errorf("sync: bootstrap page token: %w", err)
		}
		l.state.SetPageToken(tok)
		if err := l.state.Save(l.statePath); err != nil {
			return fmt.Errorf("sync: save state after bootstrap: %w", err)
		}
		l.logger.Info("sync: bootstrapped page token")
	}

	ticker := time.NewTicker(l.interval)
	defer ticker.Stop()

	// Run one tick immediately, then on the ticker.
	if err := l.runTickWithBudget(ctx); err != nil {
		if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
			return err
		}
		l.logger.Error("sync: initial tick failed", "err", err)
	}

	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-ticker.C:
			if err := l.runTickWithBudget(ctx); err != nil {
				if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
					return err
				}
				l.logger.Error("sync: tick failed", "err", err)
			}
		}
	}
}

// runTickWithBudget executes one tick; if the tick returns
// ErrDailyBudgetExhausted it persists state and sleeps until 00:05 PT.
func (l *Looper) runTickWithBudget(ctx context.Context) error {
	err := l.tick(ctx)
	if errors.Is(err, budget.ErrDailyBudgetExhausted) {
		l.logger.Warn("sync: daily budget exhausted; sleeping until next reset")
		// Persist state so the counter day and any in-flight updates aren't lost.
		if saveErr := l.state.Save(l.statePath); saveErr != nil {
			l.logger.Error("sync: save state after budget exhaustion", "err", saveErr)
		}
		reset := nextBudgetReset(l.now())
		dur := reset.Sub(l.now())
		return l.sleep(ctx, dur)
	}
	return err
}

// Reindex enumerates one folder (or every whitelisted folder when folderID is
// empty) and enqueues every file for re-indexing. This is exposed for the
// admin/reindex HTTP handler.
func (l *Looper) Reindex(ctx context.Context, folderID string) error {
	folders := l.whitelisted
	if folderID != "" {
		// Only allow reindexing a folder that is itself whitelisted (or a
		// descendant thereof). We intentionally don't verify descendancy here
		// because the admin endpoint is local-only; the handler can do it.
		folders = []string{folderID}
	}
	for _, fid := range folders {
		if err := l.drive.ListFolder(ctx, fid, func(f *drive.File) error {
			path, err := l.drive.AncestryPath(ctx, f.ID, l.whitelisted)
			if err != nil {
				l.logger.Warn("sync: AncestryPath failed during reindex", "fileID", f.ID, "err", err)
				path = ""
			}
			return l.queue.Enqueue(queueItemFromFile(f, path))
		}); err != nil {
			return fmt.Errorf("sync: reindex folder %s: %w", fid, err)
		}
	}
	return nil
}

// Status is a read-only snapshot of sync progress and budget usage, reported
// via the /status HTTP endpoint.
type Status struct {
	LastSync           time.Time
	DocumentCount      int
	ChunkCount         int
	QueueDepth         int
	SkippedCount       int
	EmbedTokensToday   int64
	EmbedTPMCap        int64
	FlashRequestsToday int64
	FlashDailyCap      int64
	ExtractModel       string
	EmbedModel         string
}

// Status returns the current Status snapshot. Errors fetching Stats are
// logged and reported as zero counts so /status doesn't fail when chromem-go
// hits a transient error.
func (l *Looper) Status(ctx context.Context) Status {
	snap := l.state.Snapshot()
	st := Status{
		LastSync:           snap.LastSync,
		QueueDepth:         l.queue.Len(),
		SkippedCount:       len(snap.Skipped),
		EmbedTokensToday:   snap.EmbedTokensToday,
		EmbedTPMCap:        l.embedTPMCap,
		FlashRequestsToday: snap.FlashRequestsToday,
		FlashDailyCap:      l.flashDailyCap,
		ExtractModel:       l.extractModel,
		EmbedModel:         l.embedModel,
	}
	if stats, err := l.store.Stats(ctx); err != nil {
		l.logger.Warn("sync: store.Stats failed", "err", err)
	} else {
		st.DocumentCount = stats.DocumentCount
		st.ChunkCount = stats.ChunkCount
	}
	return st
}

// nextBudgetReset returns the next 00:05 America/Los_Angeles wall-clock time
// strictly after now. Gemini's daily counter rolls at Pacific midnight; we
// sleep a few minutes past to leave a comfortable margin.
func nextBudgetReset(now time.Time) time.Time {
	pt, err := time.LoadLocation("America/Los_Angeles")
	if err != nil {
		pt = time.FixedZone("PST", -8*60*60)
	}
	n := now.In(pt)
	t := time.Date(n.Year(), n.Month(), n.Day(), 0, 5, 0, 0, pt)
	if !t.After(n) {
		t = t.AddDate(0, 0, 1)
	}
	return t
}
