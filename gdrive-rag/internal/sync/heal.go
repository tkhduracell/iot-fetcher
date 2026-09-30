package sync

import (
	"context"
	"fmt"

	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/drive"
	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/state"
)

// heal re-lists every whitelisted folder and enqueues files the index is
// missing or holds a stale copy of. drainQueue retries transient failures
// only a few times and changes.list only re-delivers files that change, so
// without this an old file that failed during a 429 burst stays out of the
// index for good. Runs at most once per healInterval.
//
// Only listing and store lookups happen here — no extract or embed calls —
// so a pass over a healthy index costs nothing against the budget.
func (l *Looper) heal(ctx context.Context) error {
	if l.healInterval <= 0 {
		return nil
	}
	snap := l.state.Snapshot()
	if !snap.LastHeal.IsZero() && l.now().Sub(snap.LastHeal) < l.healInterval {
		return nil
	}

	items, seen, err := l.walkFolders(ctx, l.whitelisted, func(f *drive.File) bool {
		return l.needsHeal(ctx, f, snap)
	})
	if err != nil {
		return err
	}
	if err := l.queue.EnqueueMany(items); err != nil {
		return fmt.Errorf("enqueue: %w", err)
	}

	l.state.SetLastHeal(l.now(), len(items))
	if err := l.state.Save(l.statePath); err != nil {
		return fmt.Errorf("save state after heal: %w", err)
	}
	l.logger.Info("sync: heal pass", "files", seen, "reenqueued", len(items))
	return nil
}

// needsHeal reports whether f should be re-enqueued: it has no chunks, or
// Drive has a newer copy than the one indexed. Files marked unindexable are
// left alone until edited. A store error skips just this file.
func (l *Looper) needsHeal(ctx context.Context, f *drive.File, snap state.Snapshot) bool {
	if at, ok := snap.Unindexable[f.ID]; ok && !f.ModifiedTime.After(at) {
		return false
	}
	indexed, ok, err := l.store.FileModifiedTime(ctx, f.ID)
	if err != nil {
		l.logger.Warn("sync: heal store lookup failed", "fileID", f.ID, "err", err)
		return false
	}
	return !ok || f.ModifiedTime.After(indexed)
}
