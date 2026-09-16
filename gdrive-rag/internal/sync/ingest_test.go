package sync

import (
	"context"
	"errors"
	"log/slog"
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
		/*chunkTokens*/ 800 /*chunkOverlap*/, 100 /*maxFileSizeMB*/, 10)
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

// --- Google-native MIME dispatch -----------------------------------------

// captureLogs swaps the looper's logger for one that records every record, so
// a test can assert on level as well as on what happened.
func captureLogs(l *Looper) *logCapture {
	c := &logCapture{}
	l.logger = slog.New(c)
	return c
}

type logCapture struct{ records []slog.Record }

func (c *logCapture) Enabled(context.Context, slog.Level) bool { return true }
func (c *logCapture) Handle(_ context.Context, r slog.Record) error {
	c.records = append(c.records, r)
	return nil
}
func (c *logCapture) WithAttrs([]slog.Attr) slog.Handler { return c }
func (c *logCapture) WithGroup(string) slog.Handler      { return c }

func (c *logCapture) atLeast(level slog.Level) []slog.Record {
	var out []slog.Record
	for _, r := range c.records {
		if r.Level >= level {
			out = append(out, r)
		}
	}
	return out
}

func nativeItem(fileID, mime string) queue.Item {
	return queue.Item{
		FileID:       fileID,
		FileName:     fileID + "-name",
		MimeType:     mime,
		ModifiedTime: time.Now().UTC(),
		FolderPath:   "root",
	}
}

// A Form or a Drawing has no text export. Drive would 403 the binary endpoint
// for either, so neither should reach Drive at all: they are skipped as a
// fact about the type, at info level, and counted once in Skipped.
func TestIngest_NativeWithoutTextIsSkippedCleanly(t *testing.T) {
	for _, mime := range []string{
		"application/vnd.google-apps.form",
		"application/vnd.google-apps.drawing",
		"application/vnd.google-apps.jam",
		"application/vnd.google-apps.shortcut",
	} {
		t.Run(mime, func(t *testing.T) {
			l, d, x, e, _, st, _ := newLooperForTest(t)
			logs := captureLogs(l)

			if err := l.ingest(context.Background(), nativeItem("f-"+mime, mime)); err != nil {
				t.Fatalf("ingest: unexpected error: %v", err)
			}

			if d.ExportCalls != 0 || d.DownloadCalls != 0 {
				t.Errorf("drive was called: export=%d download=%d", d.ExportCalls, d.DownloadCalls)
			}
			if x.Calls != 0 || e.Calls != 0 {
				t.Errorf("extractor/embedder ran: extract=%d embed=%d", x.Calls, e.Calls)
			}
			if loud := logs.atLeast(slog.LevelWarn); len(loud) != 0 {
				t.Errorf("logged at warn or above: %q", loud[0].Message)
			}

			snap := st.Snapshot()
			if len(snap.Skipped) != 1 {
				t.Fatalf("Skipped: got %d entries, want 1", len(snap.Skipped))
			}
			if !strings.Contains(snap.Skipped[0].Reason, "not-indexable") {
				t.Errorf("Skipped reason: got %q", snap.Skipped[0].Reason)
			}
		})
	}
}

// An unrecognised Docs Editors type is still a Docs Editors type: the binary
// endpoint refuses all of them, so it is exported on the default format rather
// than downloaded.
func TestIngest_UnknownNativeTypeExportsAsPlainText(t *testing.T) {
	l, d, x, _, _, _, _ := newLooperForTest(t)
	const fileID = "script-file"
	d.bodies[fileID] = []byte("function onOpen() {}")
	x.Text = strings.Repeat("some extracted text ", 20)

	item := nativeItem(fileID, "application/vnd.google-apps.script")
	if err := l.ingest(context.Background(), item); err != nil {
		t.Fatalf("ingest: %v", err)
	}

	if d.DownloadCalls != 0 {
		t.Errorf("downloaded a google-native file %d times", d.DownloadCalls)
	}
	if got := d.ExportMimes; len(got) != 1 || got[0] != "text/plain" {
		t.Errorf("export mimes: got %v, want [text/plain]", got)
	}
	if x.LastMime != "text/plain" {
		t.Errorf("extractor mime hint: got %q, want text/plain", x.LastMime)
	}
}

// The three mappings that predate the catch-all are deliberate choices, not
// accidents of ordering: markdown keeps a Doc's headings, CSV a Sheet's
// columns. A regression here is silent -- the export still succeeds.
func TestIngest_ExportMimePerNativeType(t *testing.T) {
	for _, tc := range []struct{ mime, want string }{
		{mimeGDoc, "text/markdown"},
		{mimeGSheet, "text/csv"},
		{mimeGSlides, "text/plain"},
	} {
		t.Run(tc.mime, func(t *testing.T) {
			l, d, x, _, _, _, _ := newLooperForTest(t)
			const fileID = "native"
			d.bodies[fileID] = []byte("body")
			x.Text = strings.Repeat("text ", 100)

			if err := l.ingest(context.Background(), nativeItem(fileID, tc.mime)); err != nil {
				t.Fatalf("ingest: %v", err)
			}
			if got := d.ExportMimes; len(got) != 1 || got[0] != tc.want {
				t.Errorf("export mimes: got %v, want [%s]", got, tc.want)
			}
		})
	}
}

// Non-native files are untouched by any of this: still downloaded, still
// extracted under their own MIME type.
func TestIngest_BinaryFileStillDownloads(t *testing.T) {
	l, d, x, _, _, _, _ := newLooperForTest(t)
	const fileID = "a.pdf"
	d.bodies[fileID] = []byte("%PDF-1.7")
	x.Text = strings.Repeat("pdf text ", 100)

	item := nativeItem(fileID, "application/pdf")
	if err := l.ingest(context.Background(), item); err != nil {
		t.Fatalf("ingest: %v", err)
	}
	if d.DownloadCalls != 1 || d.ExportCalls != 0 {
		t.Errorf("download=%d export=%d, want 1/0", d.DownloadCalls, d.ExportCalls)
	}
	if x.LastMime != "application/pdf" {
		t.Errorf("extractor mime hint: got %q", x.LastMime)
	}
}
