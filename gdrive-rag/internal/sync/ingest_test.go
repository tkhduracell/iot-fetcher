package sync

import (
	"context"
	"errors"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/budget"
	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/extract"
	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/queue"
	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/state"
	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/store"
)

// newLooperForTest assembles a Looper with a real state/queue/store in t.TempDir
// and the three fakes. Returns the looper plus its collaborators for assertions.
func newLooperForTest(t *testing.T) (*Looper, *fakeDrive, *fakeExtractor, *fakeEmbedder, *store.Store, *state.State, *queue.Queue) {
	t.Helper()
	dir := t.TempDir()

	st := &state.State{}
	statePath := filepath.Join(dir, "state.json")

	q, err := queue.Open(filepath.Join(dir, "queue.json"))
	if err != nil {
		t.Fatalf("queue.Open: %v", err)
	}
	s, err := store.Open(filepath.Join(dir, "store"))
	if err != nil {
		t.Fatalf("store.Open: %v", err)
	}
	t.Cleanup(func() { _ = s.Close() })

	d := newFakeDrive()
	x := &fakeExtractor{}
	e := &fakeEmbedder{}

	l := newTestLooper(st, statePath, q, s, d, x, e,
		[]string{"root-folder"},
		/*chunkTokens*/ 800, /*chunkOverlap*/ 100, /*maxFileSizeMB*/ 10)
	return l, d, x, e, s, st, q
}

// TestIngest_DedupSkipsEmbed: two ingests of the same content should only
// call the embedder once.
func TestIngest_DedupSkipsEmbed(t *testing.T) {
	l, d, x, e, s, _, _ := newLooperForTest(t)

	const fileID = "file-A"
	d.bodies[fileID] = []byte("ignored; extractor returns its own text")
	x.Text = "The quick brown fox jumps over the lazy dog. " + strings.Repeat("hello world ", 20)

	item := queue.Item{
		FileID:       fileID,
		FileName:     "doc.txt",
		MimeType:     "text/plain",
		Size:         100,
		ModifiedTime: time.Now().UTC(),
		WebViewLink:  "https://drive.google.com/doc",
		FolderPath:   "root",
	}

	ctx := context.Background()

	// First ingest: embedder runs, chunks land in store.
	if err := l.ingest(ctx, item); err != nil {
		t.Fatalf("first ingest: %v", err)
	}
	if e.Calls != 1 {
		t.Errorf("after first ingest: embedder called %d times, want 1", e.Calls)
	}

	stats, err := s.Stats(ctx)
	if err != nil {
		t.Fatalf("store.Stats: %v", err)
	}
	if stats.ChunkCount == 0 {
		t.Fatalf("expected at least 1 chunk after first ingest, got 0")
	}
	if stats.DocumentCount != 1 {
		t.Errorf("document count = %d; want 1", stats.DocumentCount)
	}

	// Second ingest with identical content: should short-circuit before Embed.
	callsBefore := e.Calls
	if err := l.ingest(ctx, item); err != nil {
		t.Fatalf("second ingest: %v", err)
	}
	if e.Calls != callsBefore {
		t.Errorf("second ingest called embedder: calls=%d, want %d (unchanged)", e.Calls, callsBefore)
	}

	// Third ingest with changed extracted text: embedder must run again.
	x.Text = x.Text + " ADDITIONAL CONTENT APPENDED"
	if err := l.ingest(ctx, item); err != nil {
		t.Fatalf("third ingest: %v", err)
	}
	if e.Calls <= callsBefore {
		t.Errorf("changed content did not re-embed: calls=%d, want > %d", e.Calls, callsBefore)
	}
}

// TestIngest_Unsupported: extractor returning ErrUnsupported should propagate.
func TestIngest_Unsupported(t *testing.T) {
	l, d, x, _, _, _, _ := newLooperForTest(t)
	const fileID = "bin-1"
	d.bodies[fileID] = []byte("\x00\x01\x02")
	x.ReturnErr = extract.ErrUnsupported

	err := l.ingest(context.Background(), queue.Item{
		FileID: fileID, FileName: "blob", MimeType: "application/octet-stream", Size: 3,
	})
	if !errors.Is(err, extract.ErrUnsupported) {
		t.Errorf("ingest returned %v; want ErrUnsupported", err)
	}
}

// TestIngest_BudgetExhausted: embed returning ErrDailyBudgetExhausted should
// propagate verbatim (no wrap) so drainQueue can requeue.
func TestIngest_BudgetExhausted(t *testing.T) {
	l, d, x, e, _, _, _ := newLooperForTest(t)
	const fileID = "big-doc"
	d.bodies[fileID] = []byte("raw")
	x.Text = strings.Repeat("abc ", 100)
	e.ReturnErr = budget.ErrDailyBudgetExhausted

	err := l.ingest(context.Background(), queue.Item{
		FileID: fileID, FileName: "big.txt", MimeType: "text/plain", Size: 3,
	})
	if !errors.Is(err, budget.ErrDailyBudgetExhausted) {
		t.Errorf("ingest returned %v; want ErrDailyBudgetExhausted", err)
	}
}

// TestIngest_TooLarge: items over MaxFileSizeMB land in Skipped and no
// extractor/embedder call is made.
func TestIngest_TooLarge(t *testing.T) {
	l, _, x, e, _, st, _ := newLooperForTest(t)
	// MaxFileSizeMB in newLooperForTest = 10. 11MB is oversize.
	oversize := int64(11 * 1024 * 1024)

	err := l.ingest(context.Background(), queue.Item{
		FileID: "huge", FileName: "huge.bin", MimeType: "application/octet-stream", Size: oversize,
	})
	if err != nil {
		t.Fatalf("ingest: %v", err)
	}
	if x.Calls != 0 {
		t.Errorf("extractor called %d times on oversize; want 0", x.Calls)
	}
	if e.Calls != 0 {
		t.Errorf("embedder called %d times on oversize; want 0", e.Calls)
	}
	snap := st.Snapshot()
	if len(snap.Skipped) != 1 {
		t.Fatalf("expected 1 skipped file; got %d", len(snap.Skipped))
	}
	if !strings.Contains(snap.Skipped[0].Reason, "too-large") {
		t.Errorf("skip reason = %q; want to contain 'too-large'", snap.Skipped[0].Reason)
	}
}

// TestIngest_EmptyText: empty extraction results in a DeleteFile + skip, no embed.
func TestIngest_EmptyText(t *testing.T) {
	l, d, x, e, _, st, _ := newLooperForTest(t)
	const fileID = "empty"
	d.bodies[fileID] = []byte("whatever")
	x.Text = ""

	err := l.ingest(context.Background(), queue.Item{
		FileID: fileID, FileName: "empty.txt", MimeType: "text/plain", Size: 8,
	})
	if err != nil {
		t.Fatalf("ingest: %v", err)
	}
	if e.Calls != 0 {
		t.Errorf("embedder called %d times on empty text; want 0", e.Calls)
	}
	snap := st.Snapshot()
	found := false
	for _, s := range snap.Skipped {
		if s.FileID == fileID && s.Reason == "empty-text" {
			found = true
			break
		}
	}
	if !found {
		t.Errorf("expected an 'empty-text' skip for %s; got %+v", fileID, snap.Skipped)
	}
}
