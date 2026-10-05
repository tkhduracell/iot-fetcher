// Command watchdog restarts unhealthy containers (capped per 24h) and
// auto-updates labelled containers when their image tag moves. It replaces
// WUD on rpi5.
package main

import (
	"context"
	"log/slog"
	"os"
	"os/signal"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"

	"github.com/distribution/reference"

	"github.com/tkhduracell/iot-fetcher/watchdog/internal/config"
	"github.com/tkhduracell/iot-fetcher/watchdog/internal/dockerx"
	"github.com/tkhduracell/iot-fetcher/watchdog/internal/health"
	"github.com/tkhduracell/iot-fetcher/watchdog/internal/metrics"
	"github.com/tkhduracell/iot-fetcher/watchdog/internal/update"
)

func main() {
	log := slog.New(slog.NewJSONHandler(os.Stdout, nil))
	if err := run(log); err != nil {
		log.Error("fatal", "err", err)
		os.Exit(1)
	}
}

func run(log *slog.Logger) error {
	cfg, err := config.Load()
	if err != nil {
		return err
	}
	ctx, cancel := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer cancel()

	dc, err := dockerx.New()
	if err != nil {
		return err
	}
	var mw metrics.Writer = metrics.Nop{}
	if cfg.InfluxHost != "" {
		mw = metrics.NewHTTP(cfg.InfluxHost, cfg.InfluxToken, cfg.InfluxDatabase)
	}
	limiter, err := health.NewLimiter(cfg.RestartMax, cfg.RestartWindow, cfg.StatePath)
	if err != nil {
		return err
	}

	// lock keeps a health restart from racing an in-progress recreate.
	lock := &sync.Mutex{}
	checker := &health.Checker{
		Docker: dc, Limiter: limiter, Metrics: mw, Log: log,
		Grace: cfg.UnhealthyGrace, AllHealthy: cfg.RestartAllUnhealthy,
		DryRun: cfg.DryRun, Now: time.Now, Lock: lock,
	}
	updater := &update.Updater{
		Docker: dc, Metrics: mw, Log: log,
		Labels: cfg.UpdateLabels, SelfName: cfg.SelfName,
		Prune: cfg.Prune, DryRun: cfg.DryRun, Now: time.Now, Lock: lock,
	}

	log.Info("watchdog starting",
		"health_interval", cfg.HealthInterval.String(), "unhealthy_grace", cfg.UnhealthyGrace.String(),
		"restart_max", cfg.RestartMax, "restart_window", cfg.RestartWindow.String(),
		"restart_all_unhealthy", cfg.RestartAllUnhealthy,
		"update_interval", cfg.UpdateInterval.String(), "update_labels", cfg.UpdateLabels,
		"dry_run", cfg.DryRun)

	// The update loop runs on its own goroutine so a slow image pull never
	// delays health restarts; the two only serialise on lock while a
	// container is actually being swapped. available is the update loop's
	// latest result, read by the health loop for inventory.
	var available atomic.Pointer[map[string]bool]
	empty := map[string]bool{}
	available.Store(&empty)
	var wg sync.WaitGroup
	wg.Add(1)
	go func() {
		defer wg.Done()
		t := time.NewTicker(cfg.UpdateInterval)
		defer t.Stop()
		for {
			if avail, err := updater.Tick(ctx); err != nil {
				log.Warn("update tick failed", "err", err) // keep the last known state
			} else {
				available.Store(&avail)
			}
			select {
			case <-ctx.Done():
				return
			case <-t.C:
			}
		}
	}()

	t := time.NewTicker(cfg.HealthInterval)
	defer t.Stop()
	for {
		cs, err := checker.Tick(ctx)
		if err != nil {
			log.Warn("health tick failed", "err", err)
		} else if err := mw.Write(ctx, inventory(cs, updater.Labels, *available.Load(), time.Now())); err != nil {
			log.Warn("metrics write failed", "err", err)
		}
		select {
		case <-ctx.Done():
			log.Info("watchdog stopping")
			wg.Wait() // let an in-progress recreate finish
			return nil
		case <-t.C:
		}
	}
}

// inventory is one watchdog_container point per running container (backing
// the Grafana container table that used to read wud_containers) plus a
// watchdog_heartbeat summary.
func inventory(cs []dockerx.Container, updateLabels []string, available map[string]bool, now time.Time) []*metrics.Point {
	points := make([]*metrics.Point, 0, len(cs)+1)
	unhealthy, watched := 0, 0
	for _, c := range cs {
		name, tag := splitRef(c.Image)
		h := c.Health
		if h == "" {
			h = "none"
		}
		if h == "unhealthy" {
			unhealthy++
		}
		w := update.Watched(c.Labels, updateLabels)
		if w {
			watched++
		}
		points = append(points, metrics.NewPoint("watchdog_container").
			Tag("name", c.Name).
			Tag("image_name", name).
			Tag("image_tag", tag).
			Tag("status", c.State).
			Tag("health", h).
			Tag("auto_update", boolStr(w)).
			Tag("update_available", boolStr(available[c.Name])).
			Field("up", 1).
			At(now))
	}
	points = append(points, metrics.NewPoint("watchdog_heartbeat").
		Field("containers", len(cs)).
		Field("auto_update", watched).
		Field("unhealthy", unhealthy).
		At(now))
	return points
}

// splitRef splits an image reference into its name and tag, defaulting the
// tag to "latest". A bare image ID yields ("", "").
func splitRef(ref string) (string, string) {
	if strings.HasPrefix(ref, "sha256:") {
		return "", ""
	}
	named, err := reference.ParseNormalizedNamed(ref)
	if err != nil {
		return "", ""
	}
	tag := "latest"
	if t, ok := named.(reference.Tagged); ok {
		tag = t.Tag()
	}
	return reference.FamiliarName(named), tag
}

func boolStr(b bool) string {
	if b {
		return "true"
	}
	return "false"
}
