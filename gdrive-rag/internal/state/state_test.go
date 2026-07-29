package state

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestLoadMissingReturnsZero(t *testing.T) {
	dir := t.TempDir()
	s, err := Load(filepath.Join(dir, "nope.json"))
	if err != nil {
		t.Fatalf("Load missing file: unexpected error %v", err)
	}
	if s == nil {
		t.Fatal("Load returned nil state")
	}
	if s.PageToken != "" || s.EmbedTokensToday != 0 || len(s.Skipped) != 0 {
		t.Fatalf("zero state expected, got %+v", s)
	}
}

func TestSaveLoadRoundTrip(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "state.json")

	orig := &State{
		PageToken:           "tok-123",
		LastSync:            time.Date(2026, 4, 22, 10, 0, 0, 0, time.UTC),
		InitialSyncComplete: true,
		CounterDay:          "2026-04-22",
		EmbedTokensToday:    12345,
		FlashRequestsToday:  7,
	}
	orig.AppendSkipped(SkippedFile{FileID: "f1", FileName: "a.pdf", Reason: "too large", At: time.Unix(1700000000, 0).UTC()})

	if err := orig.Save(path); err != nil {
		t.Fatalf("Save: %v", err)
	}

	got, err := Load(path)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	snap := got.Snapshot()
	if snap.PageToken != "tok-123" {
		t.Errorf("PageToken: got %q want %q", snap.PageToken, "tok-123")
	}
	if !snap.LastSync.Equal(orig.LastSync) {
		t.Errorf("LastSync: got %v want %v", snap.LastSync, orig.LastSync)
	}
	if !snap.InitialSyncComplete {
		t.Errorf("InitialSyncComplete: got false want true")
	}
	if snap.CounterDay != "2026-04-22" {
		t.Errorf("CounterDay: got %q", snap.CounterDay)
	}
	if snap.EmbedTokensToday != 12345 {
		t.Errorf("EmbedTokensToday: got %d", snap.EmbedTokensToday)
	}
	if snap.FlashRequestsToday != 7 {
		t.Errorf("FlashRequestsToday: got %d", snap.FlashRequestsToday)
	}
	if len(snap.Skipped) != 1 || snap.Skipped[0].FileID != "f1" {
		t.Errorf("Skipped: got %+v", snap.Skipped)
	}
}

func TestSaveAtomicOverwrite(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "state.json")

	s1 := &State{PageToken: "v1"}
	if err := s1.Save(path); err != nil {
		t.Fatal(err)
	}
	s2 := &State{PageToken: "v2"}
	if err := s2.Save(path); err != nil {
		t.Fatal(err)
	}
	got, err := Load(path)
	if err != nil {
		t.Fatal(err)
	}
	if got.PageToken != "v2" {
		t.Errorf("expected newest data v2, got %q", got.PageToken)
	}
	// .tmp file must not linger.
	if _, err := os.Stat(path + ".tmp"); err == nil {
		t.Errorf("stale .tmp file left behind")
	}
}

func TestAddEmbedTokensRollover(t *testing.T) {
	pt, _ := time.LoadLocation("America/Los_Angeles")
	if pt == nil {
		t.Skip("Pacific tz not available")
	}
	day1 := time.Date(2026, 4, 22, 12, 0, 0, 0, pt)
	day2 := time.Date(2026, 4, 23, 0, 10, 0, 0, pt) // 00:10 PT next day

	cur := day1
	s := &State{}
	s.SetNow(func() time.Time { return cur })

	s.AddEmbedTokens(100)
	s.AddFlashRequest()
	snap := s.Snapshot()
	if snap.CounterDay != "2026-04-22" {
		t.Fatalf("CounterDay day1: got %q", snap.CounterDay)
	}
	if snap.EmbedTokensToday != 100 || snap.FlashRequestsToday != 1 {
		t.Fatalf("day1 counters: got %+v", snap)
	}

	cur = day2
	s.AddEmbedTokens(50)
	snap = s.Snapshot()
	if snap.CounterDay != "2026-04-23" {
		t.Fatalf("CounterDay day2: got %q", snap.CounterDay)
	}
	if snap.EmbedTokensToday != 50 {
		t.Errorf("EmbedTokensToday after rollover: got %d want 50", snap.EmbedTokensToday)
	}
	if snap.FlashRequestsToday != 0 {
		t.Errorf("FlashRequestsToday after rollover: got %d want 0", snap.FlashRequestsToday)
	}
}

func TestAppendSkippedCap(t *testing.T) {
	s := &State{}
	for i := 0; i < maxSkipped+250; i++ {
		s.AppendSkipped(SkippedFile{FileID: "f", Reason: "x"})
	}
	snap := s.Snapshot()
	if len(snap.Skipped) != maxSkipped {
		t.Errorf("cap: got %d want %d", len(snap.Skipped), maxSkipped)
	}
}

func TestSnapshotIsIndependent(t *testing.T) {
	s := &State{}
	s.AppendSkipped(SkippedFile{FileID: "f1"})
	snap := s.Snapshot()
	snap.Skipped[0].FileID = "mutated"
	snap2 := s.Snapshot()
	if snap2.Skipped[0].FileID != "f1" {
		t.Errorf("Snapshot should be independent; internal got %q", snap2.Skipped[0].FileID)
	}
}

