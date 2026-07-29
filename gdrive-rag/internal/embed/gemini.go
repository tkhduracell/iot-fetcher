// Package embed wraps the Gemini embeddings API with batching and rate-limit
// awareness so the sync loop can translate chunks into vectors.
package embed

import (
	"context"
	"errors"
	"fmt"

	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/budget"
	"google.golang.org/genai"
)

// Task types recognised by the Gemini embeddings API. These are the two
// code paths the sync loop uses: documents are indexed, queries are searched.
const (
	taskRetrievalDocument = "RETRIEVAL_DOCUMENT"
	taskRetrievalQuery    = "RETRIEVAL_QUERY"
)

// DefaultBatchSize matches the RAG_EMBED_BATCH_SIZE default in .env.template.
// The gemini-embedding-001 API caps a single request at ~20K tokens; with the
// chunker's ~800-token windows, 25 chunks/request keeps us comfortably under
// that cap.
const DefaultBatchSize = 25

// runesPerToken is the same rune/token approximation the chunker uses.
// Keep these in sync: tests cross-check the helper against this constant.
const runesPerToken = 4

// embedFn is the function signature for the underlying Gemini call. It is
// unexported so production code always hits the real SDK; tests inject a
// stub via withEmbedFn to exercise EmbedBatch without the network.
type embedFn func(ctx context.Context, model string, texts []string, taskType string) ([][]float32, error)

// Config configures a Client. APIKey is required; all other fields have
// sensible defaults.
type Config struct {
	// APIKey is the GEMINI_API_KEY used to authenticate with the Gemini API.
	APIKey string
	// Model is the embedding model name, e.g. "gemini-embedding-001".
	Model string
	// BatchSize caps the number of texts per API call. Defaults to
	// DefaultBatchSize when <= 0. EmbedBatch splits larger inputs internally.
	BatchSize int
	// TPMLimiter is a token-per-minute bucket applied before each API call.
	// Nil disables rate limiting.
	TPMLimiter *budget.RateLimiter
	// RecordTokens is called with the estimated token count of each
	// successful sub-batch. Nil disables accounting. The callback must be
	// safe for concurrent use if Client is shared across goroutines.
	RecordTokens func(int64)
}

// Client wraps the Gemini embeddings API with batching and TPM throttling.
// It is safe for concurrent use: the underlying genai.Client handles its
// own locking, and RateLimiter/RecordTokens are required to be concurrent-safe.
type Client struct {
	genai     *genai.Client
	model     string
	batchSize int
	tpm       *budget.RateLimiter
	record    func(int64)

	// embed is the function invoked for each sub-batch. Defaults to a thin
	// wrapper over c.genai.Models.EmbedContent; tests can override via
	// withEmbedFn for deterministic coverage without network calls.
	embed embedFn
}

// NewClient builds a Client, dialling Gemini via the genai SDK. Returns an
// error if APIKey is empty or the SDK fails to initialise.
func NewClient(ctx context.Context, cfg Config) (*Client, error) {
	if cfg.APIKey == "" {
		return nil, errors.New("embed: APIKey is required")
	}
	if cfg.Model == "" {
		return nil, errors.New("embed: Model is required")
	}

	batch := cfg.BatchSize
	if batch <= 0 {
		batch = DefaultBatchSize
	}

	gc, err := genai.NewClient(ctx, &genai.ClientConfig{
		APIKey:  cfg.APIKey,
		Backend: genai.BackendGeminiAPI,
	})
	if err != nil {
		return nil, fmt.Errorf("embed: genai.NewClient: %w", err)
	}

	c := &Client{
		genai:     gc,
		model:     cfg.Model,
		batchSize: batch,
		tpm:       cfg.TPMLimiter,
		record:    cfg.RecordTokens,
	}
	c.embed = c.defaultEmbed
	return c, nil
}

// Close releases any underlying resources. The genai.Client currently holds
// only an HTTP client, so this is a no-op today; it exists so callers can
// use defer cleanly and so we can swap in a client with teardown later.
func (c *Client) Close() error {
	return nil
}

// EmbedBatch embeds texts using TaskType=RETRIEVAL_DOCUMENT (the indexing
// path). If len(texts) exceeds the configured BatchSize, it splits into
// consecutive sub-batches; each sub-batch:
//
//  1. Estimates its token count (runes/4, summed across the sub-batch).
//  2. Awaits the TPM limiter with that cost (no-op if nil).
//  3. Calls Gemini EmbedContent.
//  4. On success, reports the token count to RecordTokens (if set).
//
// On any sub-batch failure the wrapped error is returned immediately; earlier
// successful sub-batches' tokens remain recorded but no partial result is
// returned to the caller.
//
// Empty input (nil or []string{}) returns ([][]float32{}, nil) without making
// any API call. Callers are responsible for not passing empty strings — the
// behaviour there is whatever Gemini returns.
func (c *Client) EmbedBatch(ctx context.Context, texts []string) ([][]float32, error) {
	if len(texts) == 0 {
		return [][]float32{}, nil
	}
	return c.embedAll(ctx, texts, taskRetrievalDocument)
}

// Embed is a convenience wrapper around EmbedBatch for a single document text.
func (c *Client) Embed(ctx context.Context, text string) ([]float32, error) {
	vecs, err := c.EmbedBatch(ctx, []string{text})
	if err != nil {
		return nil, err
	}
	if len(vecs) != 1 {
		return nil, fmt.Errorf("embed: expected 1 vector, got %d", len(vecs))
	}
	return vecs[0], nil
}

// EmbedQuery embeds a single text as a search query (TaskType=RETRIEVAL_QUERY).
// This is the path used by the MCP/API search endpoint.
func (c *Client) EmbedQuery(ctx context.Context, text string) ([]float32, error) {
	vecs, err := c.embedAll(ctx, []string{text}, taskRetrievalQuery)
	if err != nil {
		return nil, err
	}
	if len(vecs) != 1 {
		return nil, fmt.Errorf("embed: expected 1 vector, got %d", len(vecs))
	}
	return vecs[0], nil
}

// embedAll is the shared batching engine for EmbedBatch and EmbedQuery.
// It splits texts into sub-batches of at most c.batchSize and makes one
// API call per sub-batch, applying the TPM limiter and recording tokens on
// success.
func (c *Client) embedAll(ctx context.Context, texts []string, taskType string) ([][]float32, error) {
	subBatches := splitBatches(texts, c.batchSize)
	out := make([][]float32, 0, len(texts))

	for _, sub := range subBatches {
		cost := estimateTokens(sub)
		if c.tpm != nil && cost > 0 {
			if err := c.tpm.Wait(ctx, int(cost)); err != nil {
				return nil, fmt.Errorf("embed: TPM wait: %w", err)
			}
		}

		vecs, err := c.embed(ctx, c.model, sub, taskType)
		if err != nil {
			return nil, fmt.Errorf("embed: Gemini EmbedContent (batch of %d, ~%d tokens): %w", len(sub), cost, err)
		}
		if len(vecs) != len(sub) {
			return nil, fmt.Errorf("embed: got %d vectors for batch of %d texts", len(vecs), len(sub))
		}

		if c.record != nil && cost > 0 {
			c.record(cost)
		}
		out = append(out, vecs...)
	}

	return out, nil
}

// defaultEmbed is the production embedFn. It marshals texts into genai.Content
// values, invokes EmbedContent, and unpacks the returned vectors.
func (c *Client) defaultEmbed(ctx context.Context, model string, texts []string, taskType string) ([][]float32, error) {
	contents := make([]*genai.Content, len(texts))
	for i, t := range texts {
		contents[i] = genai.NewContentFromText(t, genai.RoleUser)
	}
	resp, err := c.genai.Models.EmbedContent(ctx, model, contents, &genai.EmbedContentConfig{
		TaskType: taskType,
	})
	if err != nil {
		return nil, err
	}
	if resp == nil {
		return nil, errors.New("embed: nil response from Gemini")
	}
	vecs := make([][]float32, len(resp.Embeddings))
	for i, e := range resp.Embeddings {
		if e == nil {
			return nil, fmt.Errorf("embed: nil embedding at index %d", i)
		}
		vecs[i] = e.Values
	}
	return vecs, nil
}

// estimateTokens approximates the token cost of a batch as
// sum(runes(t))/runesPerToken. Matches the chunker's approximation.
func estimateTokens(texts []string) int64 {
	var runes int64
	for _, t := range texts {
		runes += int64(len([]rune(t)))
	}
	return runes / runesPerToken
}

// splitBatches chops texts into consecutive sub-batches of at most size
// elements. size <= 0 is treated as "one big batch". Returns nil for empty
// input.
func splitBatches(texts []string, size int) [][]string {
	if len(texts) == 0 {
		return nil
	}
	if size <= 0 {
		return [][]string{texts}
	}
	n := (len(texts) + size - 1) / size
	out := make([][]string, 0, n)
	for i := 0; i < len(texts); i += size {
		end := i + size
		if end > len(texts) {
			end = len(texts)
		}
		out = append(out, texts[i:end])
	}
	return out
}
