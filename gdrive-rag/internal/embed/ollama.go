package embed

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

// Embedder is what the sync loop and the search path need from an embedding
// backend. *Client (Gemini) and *OllamaClient both satisfy it.
type Embedder interface {
	EmbedBatch(ctx context.Context, texts []string) ([][]float32, error)
	EmbedQuery(ctx context.Context, text string) ([]float32, error)
	Close() error
}

// ollamaNumCtx is the context window asked for per request. Chunks are ~800
// tokens; the server's own default (OLLAMA_CONTEXT_LENGTH) is sized for chat
// models and would allocate far more memory than an embedding needs.
const ollamaNumCtx = 4096

// ollamaTimeout bounds one /api/embed call. The model runs on the Pi's CPU,
// and the first call also loads it from disk.
const ollamaTimeout = 5 * time.Minute

// OllamaConfig configures an OllamaClient.
type OllamaConfig struct {
	// URL is the Ollama server, e.g. "http://ollama:11434".
	URL string
	// Model is the embedding model tag, e.g. "qwen3-embedding:0.6b".
	Model string
	// BatchSize caps texts per request; DefaultBatchSize when <= 0.
	BatchSize int
	// QueryPrefix is prepended to search queries only. Qwen3-Embedding is
	// instruction-tuned on the query side and expects documents bare.
	QueryPrefix string
	// RecordTokens receives the prompt token count of each request. Optional.
	RecordTokens func(int64)
}

// OllamaClient embeds through a local Ollama server's /api/embed. No quota,
// no rate limiting: the only limit is the machine it runs on.
type OllamaClient struct {
	url         string
	model       string
	batchSize   int
	queryPrefix string
	record      func(int64)
	http        *http.Client
}

// NewOllamaClient validates cfg and returns a client. It does not dial.
func NewOllamaClient(cfg OllamaConfig) (*OllamaClient, error) {
	if cfg.URL == "" {
		return nil, errors.New("embed: Ollama URL is required")
	}
	if cfg.Model == "" {
		return nil, errors.New("embed: Model is required")
	}
	batch := cfg.BatchSize
	if batch <= 0 {
		batch = DefaultBatchSize
	}
	return &OllamaClient{
		url:         strings.TrimRight(cfg.URL, "/"),
		model:       cfg.Model,
		batchSize:   batch,
		queryPrefix: cfg.QueryPrefix,
		record:      cfg.RecordTokens,
		http:        &http.Client{Timeout: ollamaTimeout},
	}, nil
}

// Close is a no-op; it exists to satisfy Embedder.
func (c *OllamaClient) Close() error { return nil }

// EmbedBatch embeds document texts.
func (c *OllamaClient) EmbedBatch(ctx context.Context, texts []string) ([][]float32, error) {
	out := make([][]float32, 0, len(texts))
	for _, sub := range splitBatches(texts, c.batchSize) {
		vecs, err := c.embed(ctx, sub)
		if err != nil {
			return nil, fmt.Errorf("embed: Ollama %s (batch of %d): %w", c.model, len(sub), err)
		}
		out = append(out, vecs...)
	}
	return out, nil
}

// EmbedQuery embeds one search query, with the query instruction prefixed.
func (c *OllamaClient) EmbedQuery(ctx context.Context, text string) ([]float32, error) {
	vecs, err := c.embed(ctx, []string{c.queryPrefix + text})
	if err != nil {
		return nil, fmt.Errorf("embed: Ollama %s query: %w", c.model, err)
	}
	return vecs[0], nil
}

type ollamaEmbedRequest struct {
	Model   string         `json:"model"`
	Input   []string       `json:"input"`
	Options map[string]any `json:"options,omitempty"`
}

type ollamaEmbedResponse struct {
	Embeddings      [][]float32 `json:"embeddings"`
	PromptEvalCount int64       `json:"prompt_eval_count"`
	Error           string      `json:"error"`
}

func (c *OllamaClient) embed(ctx context.Context, texts []string) ([][]float32, error) {
	body, err := json.Marshal(ollamaEmbedRequest{
		Model:   c.model,
		Input:   texts,
		Options: map[string]any{"num_ctx": ollamaNumCtx},
	})
	if err != nil {
		return nil, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.url+"/api/embed", bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := c.http.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(resp.Body, 64<<20))
	if err != nil {
		return nil, err
	}
	var parsed ollamaEmbedResponse
	if err := json.Unmarshal(raw, &parsed); err != nil {
		return nil, fmt.Errorf("HTTP %d: decode: %w", resp.StatusCode, err)
	}
	if resp.StatusCode != http.StatusOK || parsed.Error != "" {
		return nil, fmt.Errorf("HTTP %d: %s", resp.StatusCode, parsed.Error)
	}
	if len(parsed.Embeddings) != len(texts) {
		return nil, fmt.Errorf("got %d embeddings for %d inputs", len(parsed.Embeddings), len(texts))
	}
	if c.record != nil {
		c.record(parsed.PromptEvalCount)
	}
	return parsed.Embeddings, nil
}
