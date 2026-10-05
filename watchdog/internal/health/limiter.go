package health

import (
	"encoding/json"
	"errors"
	"io/fs"
	"os"
	"path/filepath"
	"sync"
	"time"
)

// Limiter allows at most Max restarts per container in a rolling Window.
// History is persisted to Path (when set) so the window survives a restart
// of the watchdog itself.
type Limiter struct {
	Max    int
	Window time.Duration
	Path   string

	mu      sync.Mutex
	history map[string][]time.Time
}

func NewLimiter(max int, window time.Duration, path string) (*Limiter, error) {
	l := &Limiter{Max: max, Window: window, Path: path, history: map[string][]time.Time{}}
	if path == "" {
		return l, nil
	}
	b, err := os.ReadFile(path)
	if errors.Is(err, fs.ErrNotExist) {
		return l, nil
	}
	if err != nil {
		return nil, err
	}
	if err := json.Unmarshal(b, &l.history); err != nil {
		return nil, err
	}
	return l, nil
}

// Allow reports whether name may be restarted at now, and how many restarts
// it already has inside the window.
func (l *Limiter) Allow(name string, now time.Time) (bool, int) {
	l.mu.Lock()
	defer l.mu.Unlock()
	n := len(l.prune(name, now))
	return n < l.Max, n
}

// Counts is the number of restarts per container inside the window at now.
func (l *Limiter) Counts(now time.Time) map[string]int {
	l.mu.Lock()
	defer l.mu.Unlock()
	out := map[string]int{}
	for name := range l.history {
		if n := len(l.prune(name, now)); n > 0 {
			out[name] = n
		}
	}
	return out
}

// Record notes a restart of name at now and persists the history.
func (l *Limiter) Record(name string, now time.Time) error {
	l.mu.Lock()
	defer l.mu.Unlock()
	l.history[name] = append(l.prune(name, now), now)
	return l.save()
}

func (l *Limiter) prune(name string, now time.Time) []time.Time {
	kept := l.history[name][:0]
	for _, t := range l.history[name] {
		if now.Sub(t) < l.Window {
			kept = append(kept, t)
		}
	}
	if len(kept) == 0 {
		delete(l.history, name)
		return nil
	}
	l.history[name] = kept
	return kept
}

func (l *Limiter) save() error {
	if l.Path == "" {
		return nil
	}
	b, err := json.Marshal(l.history)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(l.Path), 0o755); err != nil {
		return err
	}
	tmp := l.Path + ".tmp"
	if err := os.WriteFile(tmp, b, 0o644); err != nil {
		return err
	}
	return os.Rename(tmp, l.Path)
}
