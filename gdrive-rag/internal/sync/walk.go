package sync

import (
	"context"
	"fmt"

	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/drive"
	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/queue"
)

// walkFolders lists every file under folders and returns a queue item for
// each one keep accepts (nil keeps all), plus the total number of files seen.
// Items are returned rather than enqueued so callers can persist them with a
// single EnqueueMany.
func (l *Looper) walkFolders(ctx context.Context, folders []string, keep func(*drive.File) bool) ([]queue.Item, int, error) {
	var items []queue.Item
	seen := 0
	for _, folderID := range folders {
		if err := ctx.Err(); err != nil {
			return nil, seen, err
		}
		err := l.drive.ListFolder(ctx, folderID, func(f *drive.File) error {
			if f == nil || f.ID == "" {
				return nil
			}
			seen++
			if keep != nil && !keep(f) {
				return nil
			}
			path, err := l.drive.AncestryPath(ctx, f.ID, l.whitelisted)
			if err != nil {
				l.logger.Warn("sync: AncestryPath failed", "fileID", f.ID, "err", err)
				path = ""
			}
			items = append(items, queueItemFromFile(f, path))
			return nil
		})
		if err != nil {
			return nil, seen, fmt.Errorf("list folder %s: %w", folderID, err)
		}
	}
	return items, seen, nil
}
