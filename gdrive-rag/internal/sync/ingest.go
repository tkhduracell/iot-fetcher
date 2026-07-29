package sync

import (
	"context"
	"errors"
	"fmt"

	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/budget"
	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/chunk"
	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/extract"
	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/queue"
	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/state"
	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/store"
)

// Google-native Drive MIME types we translate to a plain-text format via the
// Export API. Anything not in this switch goes through Download.
const (
	mimeGDoc    = "application/vnd.google-apps.document"
	mimeGSheet  = "application/vnd.google-apps.spreadsheet"
	mimeGSlides = "application/vnd.google-apps.presentation"
)

// ingest processes one queued file. Returns budget.ErrDailyBudgetExhausted if
// a downstream call (extract or embed) hits the daily cap, in which case the
// caller requeues the item. Returns extract.ErrUnsupported for MIME types the
// extractor can't handle; callers record those in state.Skipped. Any other
// error is a per-file failure and is also recorded.
func (l *Looper) ingest(ctx context.Context, item queue.Item) error {
	if l.maxFileSizeMB > 0 && item.Size > 0 && item.Size > int64(l.maxFileSizeMB)*1024*1024 {
		l.state.AppendSkipped(state.SkippedFile{
			FileID:   item.FileID,
			FileName: item.FileName,
			Reason:   fmt.Sprintf("too-large (%d bytes, cap %dMB)", item.Size, l.maxFileSizeMB),
			At:       l.now(),
		})
		return nil
	}

	body, effMime, err := l.fetchBody(ctx, item)
	if err != nil {
		return fmt.Errorf("fetch body: %w", err)
	}

	text, err := l.extract.Extract(ctx, effMime, item.FileName, body)
	if err != nil {
		// Propagate sentinel errors verbatim so drainQueue's switch sees them.
		if errors.Is(err, extract.ErrUnsupported) || errors.Is(err, budget.ErrDailyBudgetExhausted) {
			return err
		}
		return fmt.Errorf("extract: %w", err)
	}

	chunks := chunk.Split(text, l.chunkTokens, l.chunkOverlap)
	if len(chunks) == 0 {
		// No content — drop any stale chunks and record a skip.
		if err := l.store.DeleteFile(ctx, item.FileID); err != nil {
			l.logger.Warn("sync: deleting stale file after empty extract", "fileID", item.FileID, "err", err)
		}
		l.state.AppendSkipped(state.SkippedFile{
			FileID:   item.FileID,
			FileName: item.FileName,
			Reason:   "empty-text",
			At:       l.now(),
		})
		return nil
	}

	// Content-hash dedup.
	hashes := make([]string, len(chunks))
	for i, c := range chunks {
		hashes[i] = store.HashText(c.Text)
	}
	existing, err := l.store.ExistingHashes(ctx, item.FileID)
	if err != nil {
		return fmt.Errorf("existing hashes: %w", err)
	}
	if !needsEmbed(hashes, existing) {
		l.logger.Debug("sync: skipping embed (hashes unchanged)",
			"fileID", item.FileID, "chunks", len(chunks))
		return nil
	}

	// Embed.
	texts := make([]string, len(chunks))
	for i, c := range chunks {
		texts[i] = c.Text
	}
	vectors, err := l.embed.EmbedBatch(ctx, texts)
	if err != nil {
		if errors.Is(err, budget.ErrDailyBudgetExhausted) {
			return err
		}
		return fmt.Errorf("embed: %w", err)
	}
	if len(vectors) != len(chunks) {
		return fmt.Errorf("embed: got %d vectors for %d chunks", len(vectors), len(chunks))
	}

	// Assemble chunks for the store.
	storeChunks := make([]store.Chunk, len(chunks))
	for i, c := range chunks {
		storeChunks[i] = store.Chunk{
			FileID:       item.FileID,
			ChunkIndex:   c.Index,
			Text:         c.Text,
			Embedding:    vectors[i],
			FileName:     item.FileName,
			MimeType:     item.MimeType,
			FolderPath:   item.FolderPath,
			ModifiedTime: item.ModifiedTime,
			WebViewLink:  item.WebViewLink,
			Hash:         hashes[i],
		}
	}
	if err := l.store.ReplaceFile(ctx, item.FileID, storeChunks); err != nil {
		return fmt.Errorf("store replace: %w", err)
	}
	return nil
}

// fetchBody picks the right Drive accessor for item.MimeType and returns the
// raw bytes plus the "effective" MIME type to hand to the extractor. Google
// native docs are exported as plain text; everything else is downloaded as-is.
func (l *Looper) fetchBody(ctx context.Context, item queue.Item) (body []byte, effMime string, err error) {
	switch item.MimeType {
	case mimeGDoc:
		body, err = l.drive.Export(ctx, item.FileID, "text/markdown")
		return body, "text/markdown", err
	case mimeGSheet:
		body, err = l.drive.Export(ctx, item.FileID, "text/csv")
		return body, "text/csv", err
	case mimeGSlides:
		body, err = l.drive.Export(ctx, item.FileID, "text/plain")
		return body, "text/plain", err
	default:
		body, err = l.drive.Download(ctx, item.FileID)
		return body, item.MimeType, err
	}
}

// needsEmbed reports whether new hashes diverge from what's already stored.
// Returns true if the counts differ or any hash at the same index mismatches.
// Exported-for-test via the unexported name (same package).
func needsEmbed(hashes []string, existing map[int]string) bool {
	if len(hashes) != len(existing) {
		return true
	}
	for i, h := range hashes {
		if existing[i] != h {
			return true
		}
	}
	return false
}
