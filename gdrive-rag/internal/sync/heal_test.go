package sync

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/drive"
	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/queue"
	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/state"
)

// TestHeal_ReenqueuesOnlyMissingFiles: an indexed file and a skipped file are
// left alone; a file that was dropped (never stored) is re-enqueued.
func TestHeal_ReenqueuesOnlyMissingFiles(t *testing.T) {
	l, d, x, _, _, st, q := newLooperForTest(t)
	l.healInterval = time.Hour
	ctx := context.Background()

	d.folderFiles["root-folder"] = []*drive.File{
		{ID: "indexed", Name: "a.txt", MimeType: "text/plain"},
		{ID: "dropped", Name: "energideklaration.pdf", MimeType: "application/pdf"},
		{ID: "skipped", Name: "video", MimeType: "video/mp4"},
	}
	d.bodies["indexed"] = []byte("x")
	x.Text = strings.Repeat("hello world ", 20)
	if err := l.ingest(ctx, queue.Item{FileID: "indexed", FileName: "a.txt", MimeType: "text/plain"}); err != nil {
		t.Fatalf("ingest: %v", err)
	}
	st.AppendSkipped(state.SkippedFile{FileID: "skipped", Reason: "unsupported-mime"})

	if err := l.heal(ctx); err != nil {
		t.Fatalf("heal: %v", err)
	}
	got := q.Snapshot()
	if len(got) != 1 || got[0].FileID != "dropped" {
		t.Fatalf("queue = %+v; want only \"dropped\"", got)
	}
	if st.Snapshot().LastHeal.IsZero() {
		t.Error("LastHeal not recorded")
	}
}

// TestHeal_RespectsInterval: a second pass inside the interval is a no-op,
// and a zero interval disables healing entirely.
func TestHeal_RespectsInterval(t *testing.T) {
	l, d, _, _, _, _, q := newLooperForTest(t)
	ctx := context.Background()
	d.folderFiles["root-folder"] = []*drive.File{{ID: "dropped", MimeType: "application/pdf"}}

	l.healInterval = 0
	if err := l.heal(ctx); err != nil {
		t.Fatalf("heal: %v", err)
	}
	if q.Len() != 0 {
		t.Fatalf("disabled heal enqueued %d items", q.Len())
	}

	l.healInterval = time.Hour
	if err := l.heal(ctx); err != nil {
		t.Fatalf("heal: %v", err)
	}
	if _, _, err := q.Pop(); err != nil {
		t.Fatal(err)
	}
	if err := l.heal(ctx); err != nil {
		t.Fatalf("heal: %v", err)
	}
	if q.Len() != 0 {
		t.Fatalf("heal ran again inside interval; queue len %d", q.Len())
	}

	base := time.Now()
	l.now = func() time.Time { return base.Add(2 * time.Hour) }
	if err := l.heal(ctx); err != nil {
		t.Fatalf("heal: %v", err)
	}
	if q.Len() != 1 {
		t.Fatalf("heal after interval: queue len %d; want 1", q.Len())
	}
}
