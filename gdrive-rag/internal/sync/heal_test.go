package sync

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/drive"
	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/queue"
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
	st.MarkUnindexable("skipped", time.Time{})

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

// TestHeal_ReenqueuesStaleCopy: a file edited in Drive after it was indexed
// is re-enqueued even though chunks exist.
func TestHeal_ReenqueuesStaleCopy(t *testing.T) {
	l, d, x, _, _, _, q := newLooperForTest(t)
	l.healInterval = time.Hour
	ctx := context.Background()

	old := time.Date(2025, 1, 1, 0, 0, 0, 0, time.UTC)
	d.bodies["f"] = []byte("x")
	x.Text = strings.Repeat("hello world ", 20)
	if err := l.ingest(ctx, queue.Item{FileID: "f", MimeType: "text/plain", ModifiedTime: old}); err != nil {
		t.Fatalf("ingest: %v", err)
	}
	d.folderFiles["root-folder"] = []*drive.File{{ID: "f", MimeType: "text/plain", ModifiedTime: old.Add(time.Hour)}}

	if err := l.heal(ctx); err != nil {
		t.Fatalf("heal: %v", err)
	}
	if q.Len() != 1 {
		t.Fatalf("stale file not re-enqueued; queue len %d", q.Len())
	}
}

// TestDrain_RetriesThenGivesUp: a persistently failing file is retried on the
// next ticks, then left for heal, then marked unindexable so heal stops
// re-enqueueing it — until the file is edited.
func TestDrain_RetriesThenGivesUp(t *testing.T) {
	l, d, _, _, _, st, q := newLooperForTest(t)
	l.healInterval = time.Hour
	ctx := context.Background()
	mtime := time.Date(2025, 1, 1, 0, 0, 0, 0, time.UTC)
	item := queue.Item{FileID: "bad", MimeType: "application/pdf", ModifiedTime: mtime}
	// No body registered → Download fails every time.

	for i := 1; i < maxIngestAttempts; i++ {
		if err := q.Enqueue(item); err != nil {
			t.Fatal(err)
		}
		if err := l.drainQueue(ctx); err != nil {
			t.Fatalf("drain %d: %v", i, err)
		}
		wantQueued := i < tickRetries
		if got := q.Len() == 1; got != wantQueued {
			t.Fatalf("attempt %d: requeued=%v, want %v", i, got, wantQueued)
		}
		_, _, _ = q.Pop()
	}
	if err := q.Enqueue(item); err != nil {
		t.Fatal(err)
	}
	if err := l.drainQueue(ctx); err != nil {
		t.Fatal(err)
	}
	if _, ok := st.Snapshot().Unindexable["bad"]; !ok {
		t.Fatal("file not marked unindexable after max attempts")
	}

	d.folderFiles["root-folder"] = []*drive.File{{ID: "bad", MimeType: "application/pdf", ModifiedTime: mtime}}
	if err := l.heal(ctx); err != nil {
		t.Fatal(err)
	}
	if q.Len() != 0 {
		t.Fatal("heal re-enqueued an unindexable file")
	}

	d.folderFiles["root-folder"][0].ModifiedTime = mtime.Add(time.Hour)
	l.now = func() time.Time { return time.Now().Add(2 * time.Hour) }
	if err := l.heal(ctx); err != nil {
		t.Fatal(err)
	}
	if q.Len() != 1 {
		t.Fatal("heal ignored an edit to an unindexable file")
	}
}
