package queue

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func tmpPath(t *testing.T) string {
	t.Helper()
	return filepath.Join(t.TempDir(), "ingest_queue.json")
}

func mustOpen(t *testing.T, path string) *Queue {
	t.Helper()
	q, err := Open(path)
	if err != nil {
		t.Fatalf("Open(%q) returned error: %v", path, err)
	}
	return q
}

func TestOpen_MissingFile(t *testing.T) {
	path := tmpPath(t)
	q := mustOpen(t, path)
	if n := q.Len(); n != 0 {
		t.Fatalf("expected empty queue, got len=%d", n)
	}
	if _, ok := q.Peek(); ok {
		t.Fatalf("Peek on empty queue should return ok=false")
	}
	item, ok, err := q.Pop()
	if err != nil {
		t.Fatalf("Pop on empty queue returned error: %v", err)
	}
	if ok {
		t.Fatalf("Pop on empty queue should return ok=false, got %+v", item)
	}
}

func TestEnqueue_Pop_PrioritySmallerFirst(t *testing.T) {
	q := mustOpen(t, tmpPath(t))
	for _, it := range []Item{
		{FileID: "a", Size: 100, ModifiedTime: time.Unix(1, 0)},
		{FileID: "b", Size: 10, ModifiedTime: time.Unix(1, 0)},
		{FileID: "c", Size: 1000, ModifiedTime: time.Unix(1, 0)},
	} {
		if err := q.Enqueue(it); err != nil {
			t.Fatalf("Enqueue: %v", err)
		}
	}
	wantOrder := []string{"b", "a", "c"}
	for _, want := range wantOrder {
		got, ok, err := q.Pop()
		if err != nil {
			t.Fatalf("Pop: %v", err)
		}
		if !ok {
			t.Fatalf("Pop unexpectedly empty, wanted %s", want)
		}
		if got.FileID != want {
			t.Fatalf("pop order wrong: got %s want %s", got.FileID, want)
		}
	}
}

func TestEnqueue_Pop_TieBreakNewer(t *testing.T) {
	q := mustOpen(t, tmpPath(t))
	older := Item{FileID: "older", Size: 42, ModifiedTime: time.Unix(1_000, 0)}
	newer := Item{FileID: "newer", Size: 42, ModifiedTime: time.Unix(2_000, 0)}
	if err := q.Enqueue(older); err != nil {
		t.Fatalf("Enqueue older: %v", err)
	}
	if err := q.Enqueue(newer); err != nil {
		t.Fatalf("Enqueue newer: %v", err)
	}
	got, ok, err := q.Pop()
	if err != nil || !ok {
		t.Fatalf("Pop: ok=%v err=%v", ok, err)
	}
	if got.FileID != "newer" {
		t.Fatalf("expected newer first, got %s", got.FileID)
	}
	got, ok, err = q.Pop()
	if err != nil || !ok {
		t.Fatalf("Pop: ok=%v err=%v", ok, err)
	}
	if got.FileID != "older" {
		t.Fatalf("expected older second, got %s", got.FileID)
	}
}

func TestEnqueue_UpdateExistingByFileID(t *testing.T) {
	q := mustOpen(t, tmpPath(t))
	if err := q.Enqueue(Item{FileID: "x", Size: 100, FileName: "first"}); err != nil {
		t.Fatalf("Enqueue 1: %v", err)
	}
	if err := q.Enqueue(Item{FileID: "x", Size: 10, FileName: "second"}); err != nil {
		t.Fatalf("Enqueue 2: %v", err)
	}
	if n := q.Len(); n != 1 {
		t.Fatalf("expected dedup: len=1 got %d", n)
	}
	got, ok, err := q.Pop()
	if err != nil || !ok {
		t.Fatalf("Pop: ok=%v err=%v", ok, err)
	}
	if got.Size != 10 {
		t.Fatalf("expected updated Size=10, got %d", got.Size)
	}
	if got.FileName != "second" {
		t.Fatalf("expected updated FileName=second, got %q", got.FileName)
	}
}

func TestRemove(t *testing.T) {
	q := mustOpen(t, tmpPath(t))
	items := []Item{
		{FileID: "a", Size: 100},
		{FileID: "b", Size: 50},
		{FileID: "c", Size: 200},
	}
	if err := q.EnqueueMany(items); err != nil {
		t.Fatalf("EnqueueMany: %v", err)
	}
	// Remove the middle-priority one by id.
	if err := q.Remove("a"); err != nil {
		t.Fatalf("Remove: %v", err)
	}
	if n := q.Len(); n != 2 {
		t.Fatalf("expected len=2 after remove, got %d", n)
	}
	// Remove of a missing id is idempotent.
	if err := q.Remove("nonexistent"); err != nil {
		t.Fatalf("Remove of missing id should be nil, got %v", err)
	}
	wantOrder := []string{"b", "c"}
	for _, want := range wantOrder {
		got, ok, err := q.Pop()
		if err != nil || !ok {
			t.Fatalf("Pop: ok=%v err=%v", ok, err)
		}
		if got.FileID != want {
			t.Fatalf("pop order wrong: got %s want %s", got.FileID, want)
		}
	}
}

func TestPersistence(t *testing.T) {
	path := tmpPath(t)
	q := mustOpen(t, path)
	items := []Item{
		{FileID: "a", Size: 100, ModifiedTime: time.Unix(1, 0)},
		{FileID: "b", Size: 10, ModifiedTime: time.Unix(2, 0)},
		{FileID: "c", Size: 50, ModifiedTime: time.Unix(3, 0)},
	}
	if err := q.EnqueueMany(items); err != nil {
		t.Fatalf("EnqueueMany: %v", err)
	}

	// Reopen.
	q2 := mustOpen(t, path)
	if n := q2.Len(); n != 3 {
		t.Fatalf("expected len=3 after reopen, got %d", n)
	}
	wantOrder := []string{"b", "c", "a"}
	for _, want := range wantOrder {
		got, ok, err := q2.Pop()
		if err != nil || !ok {
			t.Fatalf("Pop: ok=%v err=%v", ok, err)
		}
		if got.FileID != want {
			t.Fatalf("pop order wrong: got %s want %s", got.FileID, want)
		}
	}
}

func TestAtomicity_Crash(t *testing.T) {
	path := tmpPath(t)
	q := mustOpen(t, path)
	if err := q.Enqueue(Item{FileID: "a", Size: 1}); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}

	// File exists at canonical path and parses cleanly as JSON array.
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("ReadFile: %v", err)
	}
	var parsed []Item
	if err := json.Unmarshal(raw, &parsed); err != nil {
		t.Fatalf("on-disk file is not valid JSON: %v\ncontents: %s", err, raw)
	}
	if len(parsed) != 1 || parsed[0].FileID != "a" {
		t.Fatalf("unexpected on-disk contents: %+v", parsed)
	}

	// No stray .tmp file should be left lying around after a successful write.
	if _, err := os.Stat(path + ".tmp"); !os.IsNotExist(err) {
		t.Fatalf("stray .tmp file present after successful Enqueue: err=%v", err)
	}
}

func TestSnapshot(t *testing.T) {
	q := mustOpen(t, tmpPath(t))
	if err := q.EnqueueMany([]Item{
		{FileID: "a", Size: 100, FileName: "orig-a"},
		{FileID: "b", Size: 50, FileName: "orig-b"},
	}); err != nil {
		t.Fatalf("EnqueueMany: %v", err)
	}

	snap := q.Snapshot()
	if len(snap) != 2 {
		t.Fatalf("snapshot len=%d, want 2", len(snap))
	}
	// Mutate the copy.
	for i := range snap {
		snap[i].FileName = "mutated"
		snap[i].Size = -1
	}

	// Internal queue should be untouched.
	if n := q.Len(); n != 2 {
		t.Fatalf("queue len changed after mutating snapshot: %d", n)
	}
	// Pop priority is still by original sizes (b=50 first).
	got, ok, err := q.Pop()
	if err != nil || !ok {
		t.Fatalf("Pop: ok=%v err=%v", ok, err)
	}
	if got.FileID != "b" || got.Size != 50 || got.FileName != "orig-b" {
		t.Fatalf("internal state corrupted by snapshot mutation: %+v", got)
	}
}

func TestEnqueue_StampsEnqueuedAt(t *testing.T) {
	q := mustOpen(t, tmpPath(t))
	before := time.Now().UTC().Add(-time.Second)
	if err := q.Enqueue(Item{FileID: "a", Size: 1}); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}
	got, ok, err := q.Pop()
	if err != nil || !ok {
		t.Fatalf("Pop: ok=%v err=%v", ok, err)
	}
	if got.EnqueuedAt.Before(before) {
		t.Fatalf("EnqueuedAt not stamped: %v (before=%v)", got.EnqueuedAt, before)
	}
}

func TestEnqueue_EmptyFileIDDropped(t *testing.T) {
	q := mustOpen(t, tmpPath(t))
	if err := q.Enqueue(Item{FileID: "", Size: 1}); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}
	if n := q.Len(); n != 0 {
		t.Fatalf("items without FileID should be dropped, got len=%d", n)
	}
}
