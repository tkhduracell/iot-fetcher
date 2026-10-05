// Package health restarts containers whose Docker healthcheck reports
// unhealthy, rate-limited per container.
package health

import (
	"context"
	"log/slog"
	"sync"
	"time"

	"github.com/tkhduracell/iot-fetcher/watchdog/internal/dockerx"
	"github.com/tkhduracell/iot-fetcher/watchdog/internal/metrics"
)

const RestartLabel = "watchdog.restart"

type Checker struct {
	Docker     dockerx.Docker
	Limiter    *Limiter
	Metrics    metrics.Writer
	Log        *slog.Logger
	Grace      time.Duration
	AllHealthy bool // restart any container with a healthcheck, not just labelled ones
	DryRun     bool
	Now        func() time.Time
	// Lock is shared with the updater; see update.Updater.Lock.
	Lock *sync.Mutex

	// unhealthySince is when each container was first seen unhealthy.
	unhealthySince map[string]time.Time
	capLogged      map[string]bool
}

// Tick checks every container once and returns the containers seen, so the
// caller can report inventory without listing twice.
func (c *Checker) Tick(ctx context.Context) ([]dockerx.Container, error) {
	if c.unhealthySince == nil {
		c.unhealthySince = map[string]time.Time{}
		c.capLogged = map[string]bool{}
	}
	if c.Lock != nil {
		c.Lock.Lock()
		defer c.Lock.Unlock()
	}
	now := c.Now()
	cs, err := c.Docker.List(ctx)
	if err != nil {
		return nil, err
	}

	seen := map[string]bool{}
	var points []*metrics.Point
	for _, ct := range cs {
		if !c.watched(ct) || ct.Health != "unhealthy" {
			continue
		}
		seen[ct.ID] = true
		since, ok := c.unhealthySince[ct.ID]
		if !ok {
			c.unhealthySince[ct.ID] = now
			c.Log.Warn("container unhealthy", "container", ct.Name)
			continue
		}
		if now.Sub(since) < c.Grace {
			continue
		}

		allowed, n := c.Limiter.Allow(ct.Name, now)
		p := metrics.NewPoint("watchdog_restart").Tag("container", ct.Name).At(now)
		if !allowed {
			// Reported every tick while capped so it stays visible, but logged
			// only once per unhealthy spell.
			if !c.capLogged[ct.ID] {
				c.capLogged[ct.ID] = true
				c.Log.Error("restart cap reached, leaving container unhealthy",
					"container", ct.Name, "restarts", n, "max", c.Limiter.Max, "window", c.Limiter.Window.String())
			}
			points = append(points, p.Field("capped", 1).Field("count", n))
			continue
		}

		c.Log.Info("restarting unhealthy container",
			"container", ct.Name, "unhealthy_for", now.Sub(since).Round(time.Second).String(), "restart", n+1, "dry_run", c.DryRun)
		delete(c.unhealthySince, ct.ID)
		if c.DryRun {
			continue // no restart, so nothing counts toward the cap
		}
		// A failed attempt still counts toward the cap, so a container that
		// can't restart isn't retried every tick forever.
		restartErr := c.Docker.Restart(ctx, ct.ID)
		if restartErr != nil {
			c.Log.Error("restart failed", "container", ct.Name, "err", restartErr)
		}
		if err := c.Limiter.Record(ct.Name, now); err != nil {
			c.Log.Warn("persist restart history failed", "err", err)
		}
		points = append(points, p.Field("capped", 0).Field("count", n+1).Field("ok", b2i(restartErr == nil)))
	}
	// Forget containers that recovered or went away.
	for id := range c.unhealthySince {
		if !seen[id] {
			delete(c.unhealthySince, id)
			delete(c.capLogged, id)
		}
	}
	// Current in-window restart count per container, every tick, so Grafana
	// can show it without its own lookback window.
	for name, n := range c.Limiter.Counts(now) {
		points = append(points, metrics.NewPoint("watchdog_restarts").
			Tag("container", name).Field("in_window", n).At(now))
	}

	if len(points) > 0 {
		if err := c.Metrics.Write(ctx, points); err != nil {
			c.Log.Warn("metrics write failed", "err", err)
		}
	}
	return cs, nil
}

func (c *Checker) watched(ct dockerx.Container) bool {
	switch ct.Labels[RestartLabel] {
	case "true":
		return true
	case "false":
		return false
	}
	return c.AllHealthy
}

func b2i(b bool) int {
	if b {
		return 1
	}
	return 0
}
