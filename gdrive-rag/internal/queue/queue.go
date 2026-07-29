// Package queue implements the persistent ingest queue that feeds work from
// drive discovery into extraction and embedding.
package queue

import (
	"container/heap"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"sync"
	"time"
)

// Item is a queued file waiting to be indexed.
type Item struct {
	FileID       string    `json:"file_id"`
	FileName     string    `json:"file_name"`
	MimeType     string    `json:"mime_type"`
	Size         int64     `json:"size"`
	ModifiedTime time.Time `json:"modified_time"`
	WebViewLink  string    `json:"web_view_link"`
	FolderPath   string    `json:"folder_path"`
	EnqueuedAt   time.Time `json:"enqueued_at"`
}

// Queue is a persistent, priority-ordered ingest queue.
//
// Priority: smaller files first; among same size, newer files (by ModifiedTime)
// first. All operations are safe for concurrent use.
//
// The canonical on-disk form is a JSON array at the path passed to Open; every
// mutation is persisted atomically via a temp file + fsync + rename.
type Queue struct {
	mu   sync.Mutex
	path string
	h    *itemHeap
	// index maps FileID -> position in the heap for O(1) dedup lookups.
	index map[string]int
}

// Open loads the queue from path, creating an empty one if the file is missing.
// The parent directory must already exist (the caller is expected to have
// ensured RAG_DATA_DIR exists).
func Open(path string) (*Queue, error) {
	q := &Queue{
		path:  path,
		h:     &itemHeap{},
		index: make(map[string]int),
	}

	data, err := os.ReadFile(path)
	if err != nil {
		if errors.Is(err, fs.ErrNotExist) {
			return q, nil
		}
		return nil, fmt.Errorf("queue: read %s: %w", path, err)
	}
	if len(data) == 0 {
		return q, nil
	}

	var items []Item
	if err := json.Unmarshal(data, &items); err != nil {
		return nil, fmt.Errorf("queue: parse %s: %w", path, err)
	}

	// Seed the heap. Dedup on load so we never keep two entries with the same
	// FileID even if the file on disk was hand-edited.
	for _, it := range items {
		if it.FileID == "" {
			continue
		}
		if _, exists := q.index[it.FileID]; exists {
			continue
		}
		q.index[it.FileID] = len(*q.h)
		*q.h = append(*q.h, it)
	}
	heap.Init(q.h)
	// After heap.Init, positions in index are stale; rebuild from the heap slice.
	q.rebuildIndex()
	return q, nil
}

// Enqueue adds item, or updates it if FileID is already present (dedup).
// Enqueued items always have their EnqueuedAt stamped if zero.
func (q *Queue) Enqueue(item Item) error {
	q.mu.Lock()
	defer q.mu.Unlock()
	q.upsertLocked(item)
	return q.persistLocked()
}

// EnqueueMany is an optimization: enqueue many items under a single fsync.
func (q *Queue) EnqueueMany(items []Item) error {
	q.mu.Lock()
	defer q.mu.Unlock()
	for _, it := range items {
		q.upsertLocked(it)
	}
	return q.persistLocked()
}

// Pop removes and returns the highest-priority item (smallest size, newest
// ModifiedTime on ties). Returns (Item{}, false, nil) when empty.
func (q *Queue) Pop() (Item, bool, error) {
	q.mu.Lock()
	defer q.mu.Unlock()
	if q.h.Len() == 0 {
		return Item{}, false, nil
	}
	top := heap.Pop(q.h).(Item)
	delete(q.index, top.FileID)
	q.rebuildIndex()
	if err := q.persistLocked(); err != nil {
		return Item{}, false, err
	}
	return top, true, nil
}

// Peek returns the top item without removing it.
func (q *Queue) Peek() (Item, bool) {
	q.mu.Lock()
	defer q.mu.Unlock()
	if q.h.Len() == 0 {
		return Item{}, false
	}
	return (*q.h)[0], true
}

// Remove removes an item by FileID. Returns nil if the file is not in the
// queue (idempotent).
func (q *Queue) Remove(fileID string) error {
	q.mu.Lock()
	defer q.mu.Unlock()
	pos, ok := q.index[fileID]
	if !ok {
		return nil
	}
	heap.Remove(q.h, pos)
	delete(q.index, fileID)
	q.rebuildIndex()
	return q.persistLocked()
}

// Len returns the current queue depth.
func (q *Queue) Len() int {
	q.mu.Lock()
	defer q.mu.Unlock()
	return q.h.Len()
}

// Snapshot returns a copy of all items currently in the queue. The returned
// slice is safe for the caller to mutate. Ordering is not guaranteed to be
// priority-ordered (it mirrors the internal heap layout); callers that need
// strict ordering should Pop in a loop or sort the snapshot themselves.
func (q *Queue) Snapshot() []Item {
	q.mu.Lock()
	defer q.mu.Unlock()
	out := make([]Item, q.h.Len())
	copy(out, *q.h)
	return out
}

// --- internals ----------------------------------------------------------

func (q *Queue) upsertLocked(item Item) {
	if item.FileID == "" {
		// Silently drop items without a FileID; there's no sensible dedup key.
		return
	}
	if item.EnqueuedAt.IsZero() {
		item.EnqueuedAt = time.Now().UTC()
	}
	if pos, ok := q.index[item.FileID]; ok {
		// Update in place then re-heapify the affected position.
		(*q.h)[pos] = item
		heap.Fix(q.h, pos)
		return
	}
	q.index[item.FileID] = q.h.Len()
	heap.Push(q.h, item)
	// Push may move items around; rebuild the positional index.
	q.rebuildIndex()
}

// rebuildIndex syncs the FileID -> position map after a heap mutation that may
// have reordered elements. O(n) but the queue is bounded to a few thousand
// items at most.
func (q *Queue) rebuildIndex() {
	for i, it := range *q.h {
		q.index[it.FileID] = i
	}
}

// persistLocked writes the current heap contents to disk atomically.
func (q *Queue) persistLocked() error {
	items := make([]Item, q.h.Len())
	copy(items, *q.h)
	data, err := json.MarshalIndent(items, "", "  ")
	if err != nil {
		return fmt.Errorf("queue: marshal: %w", err)
	}

	dir := filepath.Dir(q.path)
	if dir == "" {
		dir = "."
	}
	tmp := q.path + ".tmp"
	f, err := os.OpenFile(tmp, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, 0o644)
	if err != nil {
		return fmt.Errorf("queue: open tmp: %w", err)
	}
	if _, err := f.Write(data); err != nil {
		_ = f.Close()
		_ = os.Remove(tmp)
		return fmt.Errorf("queue: write tmp: %w", err)
	}
	if err := f.Sync(); err != nil {
		_ = f.Close()
		_ = os.Remove(tmp)
		return fmt.Errorf("queue: fsync tmp: %w", err)
	}
	if err := f.Close(); err != nil {
		_ = os.Remove(tmp)
		return fmt.Errorf("queue: close tmp: %w", err)
	}
	if err := os.Rename(tmp, q.path); err != nil {
		_ = os.Remove(tmp)
		return fmt.Errorf("queue: rename tmp: %w", err)
	}
	// Best-effort fsync on the directory so the rename is durable. On some
	// filesystems (e.g. tmpfs in tests) this may not be supported; ignore
	// ENOTSUP-style failures.
	if d, err := os.Open(dir); err == nil {
		_ = d.Sync()
		_ = d.Close()
	}
	return nil
}

// itemHeap implements heap.Interface over a slice of Item, ordered by:
//  1. Size ascending (smaller files first)
//  2. ModifiedTime descending (newer files first) on size ties
//  3. FileID ascending (stable tiebreaker so ordering is deterministic)
type itemHeap []Item

func (h itemHeap) Len() int { return len(h) }

func (h itemHeap) Less(i, j int) bool {
	a, b := h[i], h[j]
	if a.Size != b.Size {
		return a.Size < b.Size
	}
	if !a.ModifiedTime.Equal(b.ModifiedTime) {
		return a.ModifiedTime.After(b.ModifiedTime)
	}
	return a.FileID < b.FileID
}

func (h itemHeap) Swap(i, j int) { h[i], h[j] = h[j], h[i] }

func (h *itemHeap) Push(x any) { *h = append(*h, x.(Item)) }

func (h *itemHeap) Pop() any {
	old := *h
	n := len(old)
	x := old[n-1]
	*h = old[:n-1]
	return x
}
