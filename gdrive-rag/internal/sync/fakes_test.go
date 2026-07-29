package sync

import (
	"context"
	"fmt"
	"log/slog"
	"sync"
	"time"

	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/drive"
	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/queue"
	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/state"
	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/store"
)

// fakeDrive is a driveClient fake. Tests populate the public fields directly.
type fakeDrive struct {
	mu sync.Mutex

	// File bodies keyed by fileID; used for both Export and Download.
	bodies map[string][]byte
	// Files returned from ListFolder, keyed by folderID.
	folderFiles map[string][]*drive.File
	// Changes returned from ListChanges (single-call; no paging).
	changes []drive.Change
	// Whether the fileID is in the whitelist. nil means "always yes".
	inWhitelist map[string]bool
	// AncestryPath result keyed by fileID.
	ancestry map[string]string
	// pageToken is what GetStartPageToken returns and what ListChanges'
	// newToken resolves to.
	pageToken string

	// Call counters, for assertions.
	ExportCalls   int
	DownloadCalls int
}

func newFakeDrive() *fakeDrive {
	return &fakeDrive{
		bodies:      map[string][]byte{},
		folderFiles: map[string][]*drive.File{},
		inWhitelist: map[string]bool{},
		ancestry:    map[string]string{},
	}
}

func (f *fakeDrive) Export(ctx context.Context, fileID, exportMime string) ([]byte, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.ExportCalls++
	b, ok := f.bodies[fileID]
	if !ok {
		return nil, fmt.Errorf("fakeDrive: no body for %s", fileID)
	}
	return b, nil
}

func (f *fakeDrive) Download(ctx context.Context, fileID string) ([]byte, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.DownloadCalls++
	b, ok := f.bodies[fileID]
	if !ok {
		return nil, fmt.Errorf("fakeDrive: no body for %s", fileID)
	}
	return b, nil
}

func (f *fakeDrive) GetStartPageToken(ctx context.Context) (string, error) {
	return f.pageToken, nil
}

func (f *fakeDrive) ListChanges(ctx context.Context, pageToken string) ([]drive.Change, string, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	out := append([]drive.Change(nil), f.changes...)
	// Clear after delivery so subsequent ticks don't re-apply them.
	f.changes = nil
	return out, f.pageToken, nil
}

func (f *fakeDrive) IsInWhitelist(ctx context.Context, fileID string, whitelisted []string) (bool, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if v, ok := f.inWhitelist[fileID]; ok {
		return v, nil
	}
	return true, nil
}

func (f *fakeDrive) AncestryPath(ctx context.Context, fileID string, whitelisted []string) (string, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.ancestry[fileID], nil
}

func (f *fakeDrive) ListFolder(ctx context.Context, folderID string, yield func(*drive.File) error) error {
	f.mu.Lock()
	files := append([]*drive.File(nil), f.folderFiles[folderID]...)
	f.mu.Unlock()
	for _, file := range files {
		if err := yield(file); err != nil {
			return err
		}
	}
	return nil
}

// fakeExtractor returns a pre-configured text for each call. If ReturnErr is
// set it is returned regardless of input.
type fakeExtractor struct {
	Text      string
	ReturnErr error
	Calls     int
	mu        sync.Mutex
}

func (f *fakeExtractor) Extract(ctx context.Context, mimeType, fileHint string, body []byte) (string, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.Calls++
	if f.ReturnErr != nil {
		return "", f.ReturnErr
	}
	return f.Text, nil
}

// fakeEmbedder returns a deterministic vector for each text and counts calls.
// Vectors are 3-dim with the text length encoded so different inputs map to
// different vectors.
type fakeEmbedder struct {
	mu         sync.Mutex
	Calls      int
	TextsSeen  int
	ReturnErr  error
	LastVector []float32
}

func (f *fakeEmbedder) EmbedBatch(ctx context.Context, texts []string) ([][]float32, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.Calls++
	f.TextsSeen += len(texts)
	if f.ReturnErr != nil {
		return nil, f.ReturnErr
	}
	out := make([][]float32, len(texts))
	for i, t := range texts {
		// Deterministic but text-dependent.
		out[i] = []float32{float32(len(t)), float32(i), 1.0}
	}
	if len(out) > 0 {
		f.LastVector = out[len(out)-1]
	}
	return out, nil
}

// newTestLooper builds a Looper wired to injected driveClient/extractor/embedder
// fakes. The production Config accepts only concrete types; this helper exists
// for unit tests that need to stub out network dependencies.
func newTestLooper(
	st *state.State, statePath string,
	q *queue.Queue, s *store.Store,
	d driveClient, x extractor, e embedder,
	whitelisted []string,
	chunkTokens, chunkOverlap, maxFileSizeMB int,
) *Looper {
	return &Looper{
		state:         st,
		statePath:     statePath,
		queue:         q,
		store:         s,
		drive:         d,
		extract:       x,
		embed:         e,
		whitelisted:   append([]string(nil), whitelisted...),
		interval:      time.Minute,
		chunkTokens:   chunkTokens,
		chunkOverlap:  chunkOverlap,
		maxFileSizeMB: maxFileSizeMB,
		logger:        slog.New(slog.DiscardHandler),
		now:           time.Now,
		sleep:         func(_ context.Context, _ time.Duration) error { return nil },
	}
}

