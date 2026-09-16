package sync

import (
	"context"
	"errors"
	"fmt"
	"strings"

	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/budget"
	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/chunk"
	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/extract"
	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/queue"
	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/state"
	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/store"
)

// Google-native Drive MIME types we translate to a plain-text format via the
// Export API. Anything outside this family goes through Download.
const (
	mimeGDoc    = "application/vnd.google-apps.document"
	mimeGSheet  = "application/vnd.google-apps.spreadsheet"
	mimeGSlides = "application/vnd.google-apps.presentation"

	// Every Docs Editors type shares this prefix. The binary files.get
	// endpoint refuses all of them -- "Only files with binary content can be
	// downloaded. Use Export with Docs Editors files" -- so membership of this
	// family, not membership of a hand-written list, is what decides whether a
	// file is exported or downloaded.
	mimeGoogleNativePrefix = "application/vnd.google-apps."

	// Export format for a native type we have no specific mapping for. Drive
	// gained types after this code was written and will gain more; asking for
	// plain text is the guess most likely to return something a human wrote.
	defaultNativeExportMime = "text/plain"
)

// nativeExportMimes maps the Google-native types with a text-shaped export to
// the format we ask Drive for. Kept explicit because these three choices are
// deliberate: markdown keeps a Doc's headings, CSV keeps a Sheet's columns,
// and Slides only ever offered plain text.
var nativeExportMimes = map[string]string{
	mimeGDoc:    "text/markdown",
	mimeGSheet:  "text/csv",
	mimeGSlides: "text/plain",
}

// nativeWithoutText are the Docs Editors types Drive cannot export as text at
// all. A Form exports as a zipped HTML page of the *form*, not the answers; a
// Drawing and a Jamboard are pictures; the rest are references rather than
// documents. Sending any of them through Export would swap one error for
// another, so they are skipped as a fact about the type rather than logged as
// a failure to look into.
var nativeWithoutText = map[string]bool{
	"application/vnd.google-apps.form":        true,
	"application/vnd.google-apps.drawing":     true,
	"application/vnd.google-apps.jam":         true,
	"application/vnd.google-apps.site":        true,
	"application/vnd.google-apps.map":         true,
	"application/vnd.google-apps.fusiontable": true,
	"application/vnd.google-apps.folder":      true,
	"application/vnd.google-apps.shortcut":    true,
	"application/vnd.google-apps.drive-sdk":   true,
}

// exportMimeFor reports how to fetch a Google-native file: the export MIME to
// ask for, and whether the type has any text to ask for at all. The bool is
// false only for native types; a non-native MIME is not this function's
// business and is reported as exportable=false, native=false.
func exportMimeFor(mime string) (exportMime string, native, indexable bool) {
	if !strings.HasPrefix(mime, mimeGoogleNativePrefix) {
		return "", false, false
	}
	if nativeWithoutText[mime] {
		return "", true, false
	}
	if export, ok := nativeExportMimes[mime]; ok {
		return export, true, true
	}
	return defaultNativeExportMime, true, true
}

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

	if _, native, indexable := exportMimeFor(item.MimeType); native && !indexable {
		// Not a failure: this type has no text in it. Info, not warn, and it
		// belongs in Skipped so /status counts it once rather than the queue
		// churning on it every sync.
		l.logger.Info("sync: not indexable", "fileID", item.FileID, "mime", item.MimeType)
		l.state.AppendSkipped(state.SkippedFile{
			FileID:   item.FileID,
			FileName: item.FileName,
			Reason:   fmt.Sprintf("not-indexable (%s)", item.MimeType),
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
// raw bytes plus the "effective" MIME type to hand to the extractor. Every
// Google-native type is exported as text; everything else is downloaded as-is.
//
// Callers are expected to have dropped the native types with no text export
// (see exportMimeFor); one that reaches here is exported on the default
// format, which fails honestly rather than 403ing on the binary endpoint.
func (l *Looper) fetchBody(ctx context.Context, item queue.Item) (body []byte, effMime string, err error) {
	if export, native, _ := exportMimeFor(item.MimeType); native {
		if export == "" {
			export = defaultNativeExportMime
		}
		body, err = l.drive.Export(ctx, item.FileID, export)
		return body, export, err
	}
	body, err = l.drive.Download(ctx, item.FileID)
	return body, item.MimeType, err
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
