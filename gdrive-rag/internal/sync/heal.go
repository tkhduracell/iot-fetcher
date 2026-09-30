package sync

import (
	"context"
	"fmt"

	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/drive"
)

// heal re-lists every whitelisted folder and enqueues files that have no
// chunks in the store and aren't recorded as skipped. Ingest drops files on
// transient errors (429, DNS, 5xx) and changes.list only re-delivers files
// that change, so without this an old, never-edited file that failed once is
// missing from the index for good. Runs at most once per healInterval.
//
// Only listing and store lookups happen here — no extract or embed calls —
// so a pass over an already-healthy index costs nothing against the budget.
func (l *Looper) heal(ctx context.Context) error {
	if l.healInterval <= 0 {
		return nil
	}
	snap := l.state.Snapshot()
	if !snap.LastHeal.IsZero() && l.now().Sub(snap.LastHeal) < l.healInterval {
		return nil
	}

	skipped := make(map[string]struct{}, len(snap.Skipped))
	for _, sk := range snap.Skipped {
		skipped[sk.FileID] = struct{}{}
	}

	var seen, missing int
	for _, folderID := range l.whitelisted {
		err := l.drive.ListFolder(ctx, folderID, func(f *drive.File) error {
			if f == nil || f.ID == "" {
				return nil
			}
			seen++
			if _, ok := skipped[f.ID]; ok {
				return nil
			}
			existing, err := l.store.ExistingHashes(ctx, f.ID)
			if err != nil {
				return fmt.Errorf("existing hashes %s: %w", f.ID, err)
			}
			if len(existing) > 0 {
				return nil
			}
			path, err := l.drive.AncestryPath(ctx, f.ID, l.whitelisted)
			if err != nil {
				l.logger.Warn("sync: heal AncestryPath failed", "fileID", f.ID, "err", err)
				path = ""
			}
			missing++
			return l.queue.Enqueue(queueItemFromFile(f, path))
		})
		if err != nil {
			return fmt.Errorf("list folder %s: %w", folderID, err)
		}
	}

	l.state.SetLastHeal(l.now())
	if err := l.state.Save(l.statePath); err != nil {
		return fmt.Errorf("save state after heal: %w", err)
	}
	l.logger.Info("sync: heal pass", "files", seen, "reenqueued", missing)
	return nil
}
