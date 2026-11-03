package sync

import (
	"context"
	"errors"
	"fmt"

	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/budget"
	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/drive"
	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/extract"
	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/queue"
)

const (
	// tickRetries is how many consecutive failures get an immediate retry on
	// the next tick; later attempts wait for the heal pass.
	tickRetries = 3
	// maxIngestAttempts is when a failing file is given up on until edited.
	maxIngestAttempts = 8
)

// tick runs one full pass: initial backfill if required, reconcile changes,
// then drain the queue through the ingest pipeline. Returns
// budget.ErrDailyBudgetExhausted to signal the caller to sleep until reset.
func (l *Looper) tick(ctx context.Context) error {
	// 1. Initial backfill on first run.
	if !l.state.Snapshot().InitialSyncComplete {
		l.logger.Info("sync: running initial backfill", "folders", len(l.whitelisted))
		if err := l.backfill(ctx); err != nil {
			// Don't mark complete on failure — we'll retry next tick.
			return fmt.Errorf("initial backfill: %w", err)
		}
		l.state.SetInitialSyncComplete(true)
		if err := l.state.Save(l.statePath); err != nil {
			return fmt.Errorf("save state after backfill: %w", err)
		}
	}

	// 2. Self-heal: re-enqueue files the index lost. Best-effort; a failed
	// pass is retried next tick and never blocks change reconciliation.
	if err := l.heal(ctx); err != nil {
		l.logger.Warn("sync: heal failed", "err", err)
	}

	// 3. Reconcile Drive changes since the last persisted pageToken.
	if err := l.reconcileChanges(ctx); err != nil {
		return err
	}

	// 4. Drain the queue. Ingest errors are per-file; only budget exhaustion
	// aborts the whole drain.
	if err := l.drainQueue(ctx); err != nil {
		return err
	}

	l.state.SetLastSync(l.now())
	if err := l.state.Save(l.statePath); err != nil {
		return fmt.Errorf("save state after tick: %w", err)
	}
	return nil
}

// reconcileChanges pulls Drive changes since state.PageToken and enqueues or
// removes affected files. Whitelisted-scope is enforced here so files outside
// any watched folder never touch the queue.
func (l *Looper) reconcileChanges(ctx context.Context) error {
	pageToken := l.state.Snapshot().PageToken
	changes, newToken, err := l.drive.ListChanges(ctx, pageToken)
	if err != nil {
		return fmt.Errorf("list changes: %w", err)
	}

	for _, ch := range changes {
		if ch.Removed || ch.File == nil || ch.File.Trashed {
			if err := l.store.DeleteFile(ctx, ch.FileID); err != nil {
				l.logger.Warn("sync: delete on change failed", "fileID", ch.FileID, "err", err)
			}
			if err := l.queue.Remove(ch.FileID); err != nil {
				l.logger.Warn("sync: queue.Remove failed", "fileID", ch.FileID, "err", err)
			}
			continue
		}

		in, err := l.drive.IsInWhitelist(ctx, ch.FileID, l.whitelisted)
		if err != nil {
			l.logger.Warn("sync: IsInWhitelist failed", "fileID", ch.FileID, "err", err)
			continue
		}
		if !in {
			continue
		}

		path, err := l.drive.AncestryPath(ctx, ch.FileID, l.whitelisted)
		if err != nil {
			l.logger.Warn("sync: AncestryPath failed", "fileID", ch.FileID, "err", err)
			path = ""
		}
		if err := l.queue.Enqueue(queueItemFromFile(ch.File, path)); err != nil {
			l.logger.Warn("sync: queue.Enqueue failed", "fileID", ch.FileID, "err", err)
		}
	}

	if newToken != "" && newToken != pageToken {
		l.state.SetPageToken(newToken)
		if err := l.state.Save(l.statePath); err != nil {
			return fmt.Errorf("save state after changes: %w", err)
		}
	}
	return nil
}

// drainQueue pops items one at a time and passes them through ingest. It
// returns early with ErrDailyBudgetExhausted when a budget gate trips,
// requeuing the in-flight item so no work is lost.
func (l *Looper) drainQueue(ctx context.Context) (err error) {
	// Files that failed transiently are retried on the next tick, not in this
	// drain — requeueing immediately would spin on a persistent 429.
	var retry []queue.Item
	defer func() {
		if len(retry) == 0 {
			return
		}
		if reErr := l.queue.EnqueueMany(retry); reErr != nil && err == nil {
			err = fmt.Errorf("requeue failed items: %w", reErr)
		}
	}()

	for {
		if err := ctx.Err(); err != nil {
			return err
		}
		item, ok, err := l.queue.Pop()
		if err != nil {
			return fmt.Errorf("queue.Pop: %w", err)
		}
		if !ok {
			return nil
		}

		err = l.ingest(ctx, item)
		switch {
		case err == nil:
			// Proceed to the next item.
		case errors.Is(err, budget.ErrDailyBudgetExhausted):
			// Requeue so we pick this file back up after reset.
			retry = append(retry, item)
			return err
		case errors.Is(err, extract.ErrUnsupported):
			l.skip(item, "unsupported-mime")
		default:
			// Transient failure (network, API 5xx, 429, etc.). Retry on the
			// next few ticks, then leave it to the daily heal pass; after
			// maxIngestAttempts it's recorded as unindexable so a file that
			// can never be processed doesn't burn OCR budget every day.
			n := l.state.RecordFailure(item.FileID)
			l.logger.Warn("sync: ingest failed", "fileID", item.FileID, "attempt", n, "err", err)
			switch {
			case n >= maxIngestAttempts:
				l.skip(item, fmt.Sprintf("failed %d attempts: %v", n, err))
			case n < tickRetries:
				retry = append(retry, item)
			}
		}
	}
}

// queueItemFromFile converts a Drive File into a queue.Item. EnqueuedAt is
// left zero so Queue.Enqueue can stamp it.
func queueItemFromFile(f *drive.File, folderPath string) queue.Item {
	return queue.Item{
		FileID:       f.ID,
		FileName:     f.Name,
		MimeType:     f.MimeType,
		Size:         f.Size,
		ModifiedTime: f.ModifiedTime,
		WebViewLink:  f.WebViewLink,
		FolderPath:   folderPath,
	}
}
