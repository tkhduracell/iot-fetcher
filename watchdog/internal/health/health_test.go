package health

import (
	"context"
	"errors"
	"io"
	"log/slog"
	"path/filepath"
	"testing"
	"time"

	"github.com/tkhduracell/iot-fetcher/watchdog/internal/dockerx"
	"github.com/tkhduracell/iot-fetcher/watchdog/internal/metrics"
)

type fakeDocker struct {
	dockerx.Docker // unimplemented methods panic
	containers     []dockerx.Container
	restarts       []string
}

func (f *fakeDocker) List(context.Context) ([]dockerx.Container, error) { return f.containers, nil }
func (f *fakeDocker) Restart(_ context.Context, id string) error {
	f.restarts = append(f.restarts, id)
	return nil
}

type fakeMetrics struct{ points []*metrics.Point }

func (m *fakeMetrics) Write(_ context.Context, p []*metrics.Point) error {
	m.points = append(m.points, p...)
	return nil
}

func quietLog() *slog.Logger { return slog.New(slog.NewTextHandler(io.Discard, nil)) }

type clock struct{ t time.Time }

func (c *clock) now() time.Time          { return c.t }
func (c *clock) advance(d time.Duration) { c.t = c.t.Add(d) }

func newChecker(t *testing.T, cs ...dockerx.Container) (*Checker, *fakeDocker, *fakeMetrics, *clock) {
	t.Helper()
	l, err := NewLimiter(3, 24*time.Hour, filepath.Join(t.TempDir(), "restarts.json"))
	if err != nil {
		t.Fatal(err)
	}
	fd := &fakeDocker{containers: cs}
	fm := &fakeMetrics{}
	clk := &clock{t: time.Date(2026, 10, 4, 12, 0, 0, 0, time.UTC)}
	return &Checker{
		Docker: fd, Limiter: l, Metrics: fm, Log: quietLog(),
		Grace: 2 * time.Minute, AllHealthy: true, Now: clk.now,
	}, fd, fm, clk
}

func tick(t *testing.T, c *Checker) {
	t.Helper()
	if _, err := c.Tick(context.Background()); err != nil {
		t.Fatal(err)
	}
}

func TestLimiter_RollingWindow(t *testing.T) {
	path := filepath.Join(t.TempDir(), "r.json")
	l, _ := NewLimiter(3, 24*time.Hour, path)
	t0 := time.Date(2026, 10, 4, 0, 0, 0, 0, time.UTC)
	for i := 0; i < 3; i++ {
		ok, _ := l.Allow("a", t0.Add(time.Duration(i)*time.Hour))
		if !ok {
			t.Fatalf("restart %d refused", i+1)
		}
		if err := l.Record("a", t0.Add(time.Duration(i)*time.Hour)); err != nil {
			t.Fatal(err)
		}
	}
	if ok, n := l.Allow("a", t0.Add(23*time.Hour)); ok || n != 3 {
		t.Fatalf("4th restart allowed=%v n=%d, want refused at 3", ok, n)
	}
	if ok, _ := l.Allow("b", t0.Add(23*time.Hour)); !ok {
		t.Fatal("cap must be per container")
	}
	// The first restart (t0) leaves the window after 24h.
	if ok, n := l.Allow("a", t0.Add(24*time.Hour)); !ok || n != 2 {
		t.Fatalf("after window allowed=%v n=%d, want allowed with 2", ok, n)
	}

	// History survives a reload.
	l2, err := NewLimiter(3, 24*time.Hour, path)
	if err != nil {
		t.Fatal(err)
	}
	if ok, _ := l2.Allow("a", t0.Add(23*time.Hour)); ok {
		t.Fatal("reloaded limiter forgot history")
	}
}

func TestTick_WaitsForGraceThenRestarts(t *testing.T) {
	c, fd, _, clk := newChecker(t, dockerx.Container{ID: "1", Name: "sonos", Health: "unhealthy"})
	tick(t, c)
	clk.advance(time.Minute)
	tick(t, c)
	if len(fd.restarts) != 0 {
		t.Fatalf("restarted inside grace: %v", fd.restarts)
	}
	clk.advance(time.Minute)
	tick(t, c)
	if len(fd.restarts) != 1 {
		t.Fatalf("restarts = %v, want 1 after grace", fd.restarts)
	}
}

func TestTick_CapsAtThreePer24h(t *testing.T) {
	c, fd, fm, clk := newChecker(t, dockerx.Container{ID: "1", Name: "sonos", Health: "unhealthy"})
	for i := 0; i < 20; i++ {
		tick(t, c)
		clk.advance(3 * time.Minute)
	}
	if len(fd.restarts) != 3 {
		t.Fatalf("restarts = %d, want capped at 3", len(fd.restarts))
	}
	capped := false
	for _, p := range fm.points {
		if p.Fields["capped"] == 1 {
			capped = true
		}
	}
	if !capped {
		t.Fatal("no capped metric emitted")
	}

	clk.advance(24 * time.Hour)
	tick(t, c)
	clk.advance(3 * time.Minute)
	tick(t, c)
	if len(fd.restarts) != 4 {
		t.Fatalf("restarts = %d, want a 4th once the window rolled", len(fd.restarts))
	}
}

func TestTick_RecoveryResetsGrace(t *testing.T) {
	ct := dockerx.Container{ID: "1", Name: "x", Health: "unhealthy"}
	c, fd, _, clk := newChecker(t, ct)
	tick(t, c)
	clk.advance(90 * time.Second)
	fd.containers[0].Health = "healthy"
	tick(t, c)
	fd.containers[0].Health = "unhealthy"
	clk.advance(90 * time.Second)
	tick(t, c)
	if len(fd.restarts) != 0 {
		t.Fatal("grace must restart from the new unhealthy spell")
	}
}

func TestTick_Labels(t *testing.T) {
	c, fd, _, clk := newChecker(t,
		dockerx.Container{ID: "1", Name: "optout", Health: "unhealthy", Labels: map[string]string{RestartLabel: "false"}},
		dockerx.Container{ID: "2", Name: "plain", Health: "unhealthy"},
		dockerx.Container{ID: "3", Name: "optin", Health: "unhealthy", Labels: map[string]string{RestartLabel: "true"}},
		dockerx.Container{ID: "4", Name: "nocheck"},
	)
	c.AllHealthy = false
	tick(t, c)
	clk.advance(3 * time.Minute)
	tick(t, c)
	if len(fd.restarts) != 1 || fd.restarts[0] != "3" {
		t.Fatalf("restarts = %v, want only the opted-in container", fd.restarts)
	}
}

func TestTick_DryRunDoesNotRestart(t *testing.T) {
	c, fd, _, clk := newChecker(t, dockerx.Container{ID: "1", Name: "x", Health: "unhealthy"})
	c.DryRun = true
	tick(t, c)
	clk.advance(3 * time.Minute)
	tick(t, c)
	if len(fd.restarts) != 0 {
		t.Fatal("dry run restarted a container")
	}
	if n := c.Limiter.Counts(clk.now()); len(n) != 0 {
		t.Fatalf("dry run counted toward the cap: %v", n)
	}
}

type failingDocker struct{ *fakeDocker }

func (f failingDocker) Restart(_ context.Context, id string) error {
	f.restarts = append(f.restarts, id)
	return errors.New("port already allocated")
}

func TestTick_FailedRestartCountsTowardCap(t *testing.T) {
	c, fd, _, clk := newChecker(t, dockerx.Container{ID: "1", Name: "x", Health: "unhealthy"})
	c.Docker = failingDocker{fd}
	for i := 0; i < 20; i++ {
		tick(t, c)
		clk.advance(3 * time.Minute)
	}
	if n := c.Limiter.Counts(clk.now())["x"]; n != 3 {
		t.Fatalf("in-window count = %d, want failed attempts capped at 3", n)
	}
}
