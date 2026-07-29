package state

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"time"
)

// maxSkipped caps the Skipped list so state.json doesn't grow unbounded.
const maxSkipped = 1000

// pacificLoc is the location used for the daily counter rollover. Gemini RPD
// resets at midnight Pacific, so all daily counters belong to that calendar day.
var pacificLoc = func() *time.Location {
	loc, err := time.LoadLocation("America/Los_Angeles")
	if err != nil {
		// Fallback: fixed -08:00. Tests and production both ship with tzdata,
		// so this should never hit in practice.
		return time.FixedZone("PST", -8*60*60)
	}
	return loc
}()

// SkippedFile records a file we chose not to index, and why.
type SkippedFile struct {
	FileID   string    `json:"file_id"`
	FileName string    `json:"file_name"`
	Reason   string    `json:"reason"`
	At       time.Time `json:"at"`
}

// Snapshot is a read-only copy of the State fields, returned by State.Snapshot.
// It deliberately omits the mutex/clock so callers can pass it by value.
type Snapshot struct {
	PageToken           string        `json:"page_token,omitempty"`
	LastSync            time.Time     `json:"last_sync,omitempty"`
	InitialSyncComplete bool          `json:"initial_sync_complete,omitempty"`
	CounterDay          string        `json:"counter_day,omitempty"`
	EmbedTokensToday    int64         `json:"embed_tokens_today,omitempty"`
	FlashRequestsToday  int64         `json:"flash_requests_today,omitempty"`
	Skipped             []SkippedFile `json:"skipped,omitempty"`
}

// State is the persistent sync progress + per-day budget tracker.
// Persisted as JSON at ${RAG_DATA_DIR}/state.json.
type State struct {
	PageToken           string        `json:"page_token,omitempty"`
	LastSync            time.Time     `json:"last_sync,omitempty"`
	InitialSyncComplete bool          `json:"initial_sync_complete,omitempty"`
	CounterDay          string        `json:"counter_day,omitempty"`
	EmbedTokensToday    int64         `json:"embed_tokens_today,omitempty"`
	FlashRequestsToday  int64         `json:"flash_requests_today,omitempty"`
	Skipped             []SkippedFile `json:"skipped,omitempty"`

	// now is the clock used for Pacific-day rollover. Tests override it.
	// Defaults to time.Now when nil.
	now func() time.Time

	mu sync.Mutex
}

// Load reads the state JSON file at path. If the file does not exist, a zero
// State is returned with no error (first-run case).
func Load(path string) (*State, error) {
	s := &State{}
	b, err := os.ReadFile(path)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return s, nil
		}
		return nil, fmt.Errorf("read state file %q: %w", path, err)
	}
	if len(b) == 0 {
		return s, nil
	}
	if err := json.Unmarshal(b, s); err != nil {
		return nil, fmt.Errorf("parse state file %q: %w", path, err)
	}
	return s, nil
}

// Save writes the state JSON to path atomically (tmp file + fsync + rename).
func (s *State) Save(path string) error {
	s.mu.Lock()
	defer s.mu.Unlock()

	b, err := json.MarshalIndent(s, "", "  ")
	if err != nil {
		return fmt.Errorf("marshal state: %w", err)
	}

	if dir := filepath.Dir(path); dir != "" {
		if err := os.MkdirAll(dir, 0o755); err != nil {
			return fmt.Errorf("mkdir %q: %w", dir, err)
		}
	}

	tmp := path + ".tmp"
	f, err := os.OpenFile(tmp, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, 0o644)
	if err != nil {
		return fmt.Errorf("open tmp %q: %w", tmp, err)
	}
	if _, err := f.Write(b); err != nil {
		_ = f.Close()
		_ = os.Remove(tmp)
		return fmt.Errorf("write tmp %q: %w", tmp, err)
	}
	if err := f.Sync(); err != nil {
		_ = f.Close()
		_ = os.Remove(tmp)
		return fmt.Errorf("fsync tmp %q: %w", tmp, err)
	}
	if err := f.Close(); err != nil {
		_ = os.Remove(tmp)
		return fmt.Errorf("close tmp %q: %w", tmp, err)
	}
	if err := os.Rename(tmp, path); err != nil {
		_ = os.Remove(tmp)
		return fmt.Errorf("rename %q -> %q: %w", tmp, path, err)
	}
	return nil
}

// Snapshot returns a copy of the state fields for read-only inspection.
// The Skipped slice is copied so callers can't mutate internal state.
func (s *State) Snapshot() Snapshot {
	s.mu.Lock()
	defer s.mu.Unlock()

	cp := Snapshot{
		PageToken:           s.PageToken,
		LastSync:            s.LastSync,
		InitialSyncComplete: s.InitialSyncComplete,
		CounterDay:          s.CounterDay,
		EmbedTokensToday:    s.EmbedTokensToday,
		FlashRequestsToday:  s.FlashRequestsToday,
	}
	if len(s.Skipped) > 0 {
		cp.Skipped = make([]SkippedFile, len(s.Skipped))
		copy(cp.Skipped, s.Skipped)
	}
	return cp
}

// SetNow overrides the clock used for Pacific-day rollover. For tests.
func (s *State) SetNow(now func() time.Time) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.now = now
}

// SetPageToken updates the Drive changes page token.
func (s *State) SetPageToken(tok string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.PageToken = tok
}

// SetLastSync records the last successful sync tick.
func (s *State) SetLastSync(t time.Time) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.LastSync = t
}

// SetInitialSyncComplete flips the first-run backfill flag.
func (s *State) SetInitialSyncComplete(done bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.InitialSyncComplete = done
}

// AddEmbedTokens increments the embedding-token counter, rolling the day over
// first if the Pacific calendar day has changed.
func (s *State) AddEmbedTokens(n int64) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.rolloverLocked()
	s.EmbedTokensToday += n
}

// AddFlashRequest increments the Flash request counter, rolling over as needed.
func (s *State) AddFlashRequest() {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.rolloverLocked()
	s.FlashRequestsToday++
}

// AppendSkipped records a skipped file. Keeps only the most recent
// maxSkipped entries; drops the oldest on overflow.
func (s *State) AppendSkipped(sk SkippedFile) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.Skipped = append(s.Skipped, sk)
	if over := len(s.Skipped) - maxSkipped; over > 0 {
		// Drop oldest `over` entries.
		s.Skipped = append([]SkippedFile(nil), s.Skipped[over:]...)
	}
}

// rolloverLocked resets daily counters if the Pacific calendar day has changed.
// Caller must hold s.mu.
func (s *State) rolloverLocked() {
	today := s.todayPacificLocked()
	if s.CounterDay != today {
		s.CounterDay = today
		s.EmbedTokensToday = 0
		s.FlashRequestsToday = 0
	}
}

// todayPacificLocked returns today's Pacific calendar day as YYYY-MM-DD.
func (s *State) todayPacificLocked() string {
	now := time.Now
	if s.now != nil {
		now = s.now
	}
	return now().In(pacificLoc).Format("2006-01-02")
}
