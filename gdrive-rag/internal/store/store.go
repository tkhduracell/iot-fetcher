// Package store wraps the chromem-go embedded vector database for persistent
// chunk storage and similarity search.
//
// The store is intentionally thin: callers own embedding generation and pass
// fully-embedded chunks in. Document IDs are always `{fileID}:{chunkIndex}`.
// Metadata is a flat map[string]string (the chromem-go constraint); callers
// interact through the typed Chunk struct and the store handles conversion.
package store

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"strconv"
	"strings"
	"sync"
	"time"

	chromem "github.com/philippgille/chromem-go"
)

const (
	collectionName = "gdrive_rag"

	metaFileID       = "file_id"
	metaFileName     = "file_name"
	metaMimeType     = "mime_type"
	metaFolderPath   = "folder_path"
	metaModifiedTime = "modified_time"
	metaChunkIndex   = "chunk_index"
	metaWebViewLink  = "web_view_link"
	metaHash         = "hash"
)

// Chunk is a single embedded chunk of a Drive file. Callers populate it
// (including Embedding and Hash) before handing it to the store.
type Chunk struct {
	FileID       string
	ChunkIndex   int
	Text         string
	Embedding    []float32
	FileName     string
	MimeType     string
	FolderPath   string
	ModifiedTime time.Time
	WebViewLink  string
	// Hash is the SHA-256 hex of Text. Use HashText to compute it.
	Hash string
}

// HashText returns the SHA-256 hex digest of s. Use this to populate
// Chunk.Hash so dedup comparisons are consistent.
func HashText(s string) string {
	sum := sha256.Sum256([]byte(s))
	return hex.EncodeToString(sum[:])
}

// ID returns the canonical chromem document ID for this chunk.
func (c Chunk) ID() string {
	return chunkID(c.FileID, c.ChunkIndex)
}

func chunkID(fileID string, idx int) string {
	return fileID + ":" + strconv.Itoa(idx)
}

// Store wraps a persistent chromem-go collection.
type Store struct {
	db         *chromem.DB
	collection *chromem.Collection

	// mu guards ReplaceFile's delete-then-insert sequence so two callers
	// replacing the same file don't interleave. chromem-go has its own
	// internal locking for individual operations, but not across them.
	mu sync.RWMutex

	// embedDim caches the embedding dimension once we've observed any chunk
	// (either via Add or via a successful probe). Protected by mu.
	embedDim int
}

// failEmbeddingFunc is installed on the collection so that if anyone ever
// tries to add a chunk without an embedding it loudly fails instead of
// silently making an API call.
func failEmbeddingFunc(_ context.Context, _ string) ([]float32, error) {
	return nil, errors.New("store: chunks must be embedded before insertion; embedding function should not be called")
}

// Open opens or creates a persistent chromem-go database rooted at dir.
// The collection name is fixed ("gdrive_rag"). Reopening the same dir restores
// all previously stored chunks.
func Open(dir string) (*Store, error) {
	if dir == "" {
		return nil, errors.New("store: dir is empty")
	}
	db, err := chromem.NewPersistentDB(dir, false)
	if err != nil {
		return nil, fmt.Errorf("store: open persistent db: %w", err)
	}
	col, err := db.GetOrCreateCollection(collectionName, nil, failEmbeddingFunc)
	if err != nil {
		return nil, fmt.Errorf("store: get/create collection: %w", err)
	}
	return &Store{db: db, collection: col}, nil
}

// Close releases resources. chromem-go persists on each write, so this is
// effectively a no-op; it exists for API symmetry.
func (s *Store) Close() error {
	return nil
}

// ExistingHashes returns map[chunkIndex]hash for every chunk currently stored
// under the given fileID. Used for dedup: if the new chunk at a given index
// has the same hash, the caller can skip re-embedding.
//
// We look up by canonical ID (`{fileID}:{idx}`) starting at idx=0 and stop at
// the first gap. Callers always write contiguous chunks starting at 0 via
// ReplaceFile, so this is sufficient and avoids chromem-go's requirement that
// QueryEmbedding's nResults <= collection size.
func (s *Store) ExistingHashes(ctx context.Context, fileID string) (map[int]string, error) {
	if fileID == "" {
		return nil, errors.New("store: fileID is empty")
	}
	s.mu.RLock()
	defer s.mu.RUnlock()

	out := make(map[int]string)
	for i := 0; ; i++ {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		doc, err := s.collection.GetByID(ctx, chunkID(fileID, i))
		if err != nil {
			// chromem returns a "not found" error; we treat the first miss
			// as end-of-chunks. There is no typed sentinel, so string-match.
			if strings.Contains(err.Error(), "not found") {
				break
			}
			return nil, fmt.Errorf("store: get chunk %d: %w", i, err)
		}
		if h, ok := doc.Metadata[metaHash]; ok {
			out[i] = h
		} else {
			out[i] = ""
		}
	}
	return out, nil
}

// ReplaceFile atomically (best-effort) replaces all chunks for fileID with
// the provided chunks. Existing chunks for the file are deleted first, then
// the new chunks are inserted. chromem-go has no transactions, so a crash
// mid-way may leave the DB with only the old data (if delete failed) or no
// data for the file (if insert failed after delete); callers should be able
// to re-drive the upsert on next sync.
//
// Passing an empty chunks slice is equivalent to DeleteFile.
func (s *Store) ReplaceFile(ctx context.Context, fileID string, chunks []Chunk) error {
	if fileID == "" {
		return errors.New("store: fileID is empty")
	}
	for i, c := range chunks {
		if c.FileID != "" && c.FileID != fileID {
			return fmt.Errorf("store: chunk %d FileID %q does not match %q", i, c.FileID, fileID)
		}
	}

	s.mu.Lock()
	defer s.mu.Unlock()

	if err := s.deleteFileLocked(ctx, fileID); err != nil {
		return err
	}
	if len(chunks) == 0 {
		return nil
	}

	ids := make([]string, len(chunks))
	embeddings := make([][]float32, len(chunks))
	metadatas := make([]map[string]string, len(chunks))
	contents := make([]string, len(chunks))
	for i, c := range chunks {
		if len(c.Embedding) == 0 {
			return fmt.Errorf("store: chunk %d missing embedding", i)
		}
		idx := c.ChunkIndex
		ids[i] = chunkID(fileID, idx)
		embeddings[i] = c.Embedding
		contents[i] = c.Text
		metadatas[i] = map[string]string{
			metaFileID:       fileID,
			metaFileName:     c.FileName,
			metaMimeType:     c.MimeType,
			metaFolderPath:   c.FolderPath,
			metaModifiedTime: c.ModifiedTime.UTC().Format(time.RFC3339Nano),
			metaChunkIndex:   strconv.Itoa(idx),
			metaWebViewLink:  c.WebViewLink,
			metaHash:         c.Hash,
		}
	}

	if err := s.collection.Add(ctx, ids, embeddings, metadatas, contents); err != nil {
		return fmt.Errorf("store: add chunks: %w", err)
	}
	if s.embedDim == 0 && len(embeddings[0]) > 0 {
		s.embedDim = len(embeddings[0])
	}
	return nil
}

// DeleteFile removes all chunks for the given fileID.
func (s *Store) DeleteFile(ctx context.Context, fileID string) error {
	if fileID == "" {
		return errors.New("store: fileID is empty")
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.deleteFileLocked(ctx, fileID)
}

func (s *Store) deleteFileLocked(ctx context.Context, fileID string) error {
	// Short-circuit: chromem Delete errors if no filter matches anything?
	// Actually it's a no-op when the collection is empty or nothing matches,
	// so we can call unconditionally — but we must pass at least one of
	// where/whereDocument/ids. We pass a where filter.
	if s.collection.Count() == 0 {
		return nil
	}
	err := s.collection.Delete(ctx, map[string]string{metaFileID: fileID}, nil)
	if err != nil {
		return fmt.Errorf("store: delete by file_id: %w", err)
	}
	return nil
}

// QueryOptions filters a similarity search.
type QueryOptions struct {
	TopK int
	// FolderPrefix, if non-empty, restricts results to chunks whose
	// FolderPath starts with this string. Applied client-side because
	// chromem-go only supports exact-match metadata filters.
	FolderPrefix string
	// MimeFilter, if non-empty, is an exact match on mime_type.
	MimeFilter string
}

// QueryResult is a single similarity-ranked chunk.
type QueryResult struct {
	Chunk      Chunk
	Similarity float32
}

// Query returns the TopK chunks most similar to embedding, subject to the
// optional filters in opts. An empty collection returns (nil, nil).
func (s *Store) Query(ctx context.Context, embedding []float32, opts QueryOptions) ([]QueryResult, error) {
	if len(embedding) == 0 {
		return nil, errors.New("store: embedding is empty")
	}
	if opts.TopK <= 0 {
		return nil, errors.New("store: TopK must be > 0")
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.embedDim == 0 {
		s.embedDim = len(embedding)
	}

	total := s.collection.Count()
	if total == 0 {
		return nil, nil
	}

	var where map[string]string
	if opts.MimeFilter != "" {
		where = map[string]string{metaMimeType: opts.MimeFilter}
	}

	// chromem-go requires nResults <= collection size. If FolderPrefix is
	// set we over-fetch (up to the full collection) because we post-filter.
	fetch := opts.TopK
	if opts.FolderPrefix != "" {
		fetch = total
	}
	if fetch > total {
		fetch = total
	}

	raw, err := s.collection.QueryEmbedding(ctx, embedding, fetch, where, nil)
	if err != nil {
		return nil, fmt.Errorf("store: query: %w", err)
	}

	out := make([]QueryResult, 0, len(raw))
	for _, r := range raw {
		ch, err := chunkFromResult(r)
		if err != nil {
			return nil, err
		}
		if opts.FolderPrefix != "" && !strings.HasPrefix(ch.FolderPath, opts.FolderPrefix) {
			continue
		}
		out = append(out, QueryResult{Chunk: ch, Similarity: r.Similarity})
		if len(out) >= opts.TopK {
			break
		}
	}
	return out, nil
}

// Stats reports high-level collection counts.
type Stats struct {
	// DocumentCount is the number of distinct Drive files (unique file_id).
	DocumentCount int
	// ChunkCount is the total number of chunks stored.
	ChunkCount int
}

// Stats returns document and chunk counts. DocumentCount requires
// enumerating chunks; since chromem-go doesn't expose an iterator, we do a
// broad QueryEmbedding using a zero-vector of the known embedding dimension.
//
// The dimension is cached after the first successful Add or Query. If the DB
// was reopened with existing data and Stats is called before any Add/Query,
// we probe common dimensions (1/384/768/1536/3072).
func (s *Store) Stats(ctx context.Context) (Stats, error) {
	s.mu.RLock()
	total := s.collection.Count()
	dim := s.embedDim
	s.mu.RUnlock()

	if total == 0 {
		return Stats{}, nil
	}

	results, err := s.enumerate(ctx, total, dim)
	if err != nil {
		return Stats{}, err
	}
	files := make(map[string]struct{}, len(results))
	for _, r := range results {
		if fid, ok := r.Metadata[metaFileID]; ok {
			files[fid] = struct{}{}
		}
	}
	return Stats{DocumentCount: len(files), ChunkCount: len(results)}, nil
}

// enumerate returns every chunk in the collection via QueryEmbedding. If dim
// is zero it probes common embedding sizes to discover the right one.
func (s *Store) enumerate(ctx context.Context, total, dim int) ([]chromem.Result, error) {
	if dim > 0 {
		probe := make([]float32, dim)
		probe[0] = 1
		return s.collection.QueryEmbedding(ctx, probe, total, nil, nil)
	}
	var lastErr error
	for _, d := range []int{768, 1536, 3072, 384, 1} {
		probe := make([]float32, d)
		probe[0] = 1
		res, err := s.collection.QueryEmbedding(ctx, probe, total, nil, nil)
		if err == nil {
			s.mu.Lock()
			if s.embedDim == 0 {
				s.embedDim = d
			}
			s.mu.Unlock()
			return res, nil
		}
		lastErr = err
	}
	return nil, fmt.Errorf("store: could not probe embedding dimension: %w", lastErr)
}

func chunkFromResult(r chromem.Result) (Chunk, error) {
	idx, err := strconv.Atoi(r.Metadata[metaChunkIndex])
	if err != nil {
		return Chunk{}, fmt.Errorf("store: parse chunk_index %q: %w", r.Metadata[metaChunkIndex], err)
	}
	var modified time.Time
	if s := r.Metadata[metaModifiedTime]; s != "" {
		if t, err := time.Parse(time.RFC3339Nano, s); err == nil {
			modified = t
		} else if t, err := time.Parse(time.RFC3339, s); err == nil {
			modified = t
		}
	}
	return Chunk{
		FileID:       r.Metadata[metaFileID],
		ChunkIndex:   idx,
		Text:         r.Content,
		Embedding:    r.Embedding,
		FileName:     r.Metadata[metaFileName],
		MimeType:     r.Metadata[metaMimeType],
		FolderPath:   r.Metadata[metaFolderPath],
		ModifiedTime: modified,
		WebViewLink:  r.Metadata[metaWebViewLink],
		Hash:         r.Metadata[metaHash],
	}, nil
}
