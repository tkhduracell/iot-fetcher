// Package update replaces WUD: it recreates labelled containers when the
// registry's digest for their tag no longer matches the local image.
package update

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"strings"
	"sync"
	"time"

	"github.com/tkhduracell/iot-fetcher/watchdog/internal/dockerx"
	"github.com/tkhduracell/iot-fetcher/watchdog/internal/metrics"
)

type Updater struct {
	Docker   dockerx.Docker
	Metrics  metrics.Writer
	Log      *slog.Logger
	Labels   []string
	SelfName string
	Prune    bool
	DryRun   bool
	Now      func() time.Time
	// Lock is held while a container is being swapped, and by the health
	// checker for each tick, so a restart never races a recreate. Pulls and
	// digest checks run outside it so a slow pull can't stall health checks.
	Lock *sync.Mutex

	// last is the previous tick's result, reused for containers whose digest
	// check fails so a registry hiccup doesn't read as "all current".
	last map[string]bool
}

// applyTimeout bounds one whole recreate (stop → create → start, or the
// rollback). It runs detached from shutdown so a SIGTERM mid-swap can't
// strand the old container stopped and renamed.
const applyTimeout = 5 * time.Minute

// Watched reports whether a container with these labels is auto-updated.
func Watched(labels map[string]string, keys []string) bool {
	for _, k := range keys {
		if labels[k] == "true" {
			return true
		}
	}
	return false
}

// Tick checks every labelled container once. It returns the names that had
// an update available, whether or not the update was applied.
func (u *Updater) Tick(ctx context.Context) (map[string]bool, error) {
	cs, err := u.Docker.List(ctx)
	if err != nil {
		return nil, err
	}
	available := map[string]bool{}
	var points []*metrics.Point
	updated := false
	for _, ct := range cs {
		if !Watched(ct.Labels, u.Labels) {
			continue
		}
		newer, err := u.hasUpdate(ctx, ct)
		if err != nil {
			u.Log.Warn("digest check failed", "container", ct.Name, "image", ct.Image, "err", err)
			if u.last[ct.Name] {
				available[ct.Name] = true
			}
			continue
		}
		if !newer {
			continue
		}
		available[ct.Name] = true
		if ct.Name == u.SelfName {
			u.Log.Info("update available for watchdog itself; not recreating in place", "image", ct.Image)
			continue
		}
		u.Log.Info("updating container", "container", ct.Name, "image", ct.Image, "dry_run", u.DryRun)
		if u.DryRun {
			continue
		}
		rolledBack, err := u.apply(ctx, ct)
		ok := 0
		if err != nil {
			u.Log.Error("update failed", "container", ct.Name, "rolled_back", rolledBack, "err", err)
		} else {
			ok = 1
			updated = true
			delete(available, ct.Name)
			u.Log.Info("container updated", "container", ct.Name)
		}
		points = append(points, metrics.NewPoint("watchdog_update").
			Tag("container", ct.Name).
			Field("ok", ok).
			Field("rolled_back", b2i(rolledBack)).
			At(u.Now()))
	}
	if updated && u.Prune {
		if err := u.Docker.Prune(ctx); err != nil {
			u.Log.Warn("image prune failed", "err", err)
		}
	}
	if len(points) > 0 {
		if err := u.Metrics.Write(ctx, points); err != nil {
			u.Log.Warn("metrics write failed", "err", err)
		}
	}
	u.last = available
	return available, nil
}

// hasUpdate compares the registry digest for the container's image reference
// against the repo digests of the image the container is running.
func (u *Updater) hasUpdate(ctx context.Context, ct dockerx.Container) (bool, error) {
	if strings.HasPrefix(ct.Image, "sha256:") {
		return false, nil // created from a bare image ID; nothing to track
	}
	remote, err := u.Docker.RemoteDigest(ctx, ct.Image)
	if err != nil {
		return false, err
	}
	local, err := u.Docker.LocalDigests(ctx, ct.ImageID)
	if err != nil {
		return false, err
	}
	if len(local) == 0 {
		return false, errors.New("local image has no repo digest (built locally?)")
	}
	for _, d := range local {
		if strings.HasSuffix(d, "@"+remote) {
			return false, nil
		}
	}
	return true, nil
}

// apply pulls the new image and recreates the container with the same spec.
// If the new container can't be created or started, the old one is renamed
// back and restarted. It reports whether a rollback happened.
func (u *Updater) apply(ctx context.Context, ct dockerx.Container) (bool, error) {
	if err := u.Docker.Pull(ctx, ct.Image); err != nil {
		return false, fmt.Errorf("pull: %w", err)
	}
	// From here on the swap must finish (or roll back) even if we're asked
	// to shut down.
	ctx, cancel := context.WithTimeout(context.WithoutCancel(ctx), applyTimeout)
	defer cancel()
	if u.Lock != nil {
		u.Lock.Lock()
		defer u.Lock.Unlock()
	}

	spec, err := u.Docker.Inspect(ctx, ct.ID)
	if err != nil {
		return false, fmt.Errorf("inspect: %w", err)
	}
	oldImage, err := u.Docker.ImageDefaults(ctx, ct.ImageID)
	if err != nil {
		return false, fmt.Errorf("inspect old image: %w", err)
	}
	dockerx.StripImageDefaults(spec.Config, oldImage)
	spec.Config.Image = ct.Image

	if err := u.Docker.Stop(ctx, ct.ID); err != nil {
		return false, fmt.Errorf("stop: %w", err)
	}
	oldName := spec.Name + "-watchdog-old"
	if err := u.Docker.Rename(ctx, ct.ID, oldName); err != nil {
		return u.rollback(ctx, ct, "", "", fmt.Errorf("rename: %w", err))
	}

	newID, err := u.Docker.Create(ctx, spec)
	if err != nil {
		return u.rollback(ctx, ct, newID, spec.Name, fmt.Errorf("create: %w", err))
	}
	if err := u.Docker.Start(ctx, newID); err != nil {
		return u.rollback(ctx, ct, newID, spec.Name, fmt.Errorf("start: %w", err))
	}
	if err := u.Docker.Remove(ctx, ct.ID); err != nil {
		u.Log.Warn("remove old container failed", "container", oldName, "err", err)
	}
	return false, nil
}

// rollback removes a half-made new container (if any), restores the old
// container's name when it was changed, and starts it again.
func (u *Updater) rollback(ctx context.Context, ct dockerx.Container, newID, name string, cause error) (bool, error) {
	if newID != "" {
		if err := u.Docker.Remove(ctx, newID); err != nil {
			u.Log.Warn("remove failed new container", "err", err)
		}
	}
	if name != "" {
		if err := u.Docker.Rename(ctx, ct.ID, name); err != nil {
			return true, fmt.Errorf("%w; rollback rename: %v", cause, err)
		}
	}
	if err := u.Docker.Start(ctx, ct.ID); err != nil {
		return true, fmt.Errorf("%w; rollback start: %v", cause, err)
	}
	return true, cause
}

func b2i(b bool) int {
	if b {
		return 1
	}
	return 0
}
