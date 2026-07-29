package sync

import (
	"context"
	"fmt"

	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/drive"
)

// backfill walks every whitelisted folder and enqueues all files it finds.
// The queue dedups by FileID, so it's safe to re-enter after a crash partway
// through.
func (l *Looper) backfill(ctx context.Context) error {
	for _, folderID := range l.whitelisted {
		if err := ctx.Err(); err != nil {
			return err
		}
		err := l.drive.ListFolder(ctx, folderID, func(f *drive.File) error {
			if f == nil || f.ID == "" {
				return nil
			}
			path, err := l.drive.AncestryPath(ctx, f.ID, l.whitelisted)
			if err != nil {
				l.logger.Warn("sync: backfill AncestryPath failed", "fileID", f.ID, "err", err)
				path = ""
			}
			return l.queue.Enqueue(queueItemFromFile(f, path))
		})
		if err != nil {
			return fmt.Errorf("list folder %s: %w", folderID, err)
		}
	}
	return nil
}
