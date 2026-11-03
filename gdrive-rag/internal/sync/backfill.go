package sync

import (
	"context"
)

// backfill walks every whitelisted folder and enqueues all files it finds.
// The queue dedups by FileID, so it's safe to re-enter after a crash partway
// through. It counts as a heal pass: everything missing is now queued.
func (l *Looper) backfill(ctx context.Context) error {
	items, _, err := l.walkFolders(ctx, l.whitelisted, nil)
	if err != nil {
		return err
	}
	if err := l.queue.EnqueueMany(items); err != nil {
		return err
	}
	l.state.SetLastHeal(l.now(), len(items))
	return nil
}
