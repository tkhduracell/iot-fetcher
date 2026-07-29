package embed

import (
	"context"

	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/budget"
)

// newTestClient builds a Client that uses the provided fake embed function,
// bypassing the real genai SDK. Intended for tests only.
//
// batchSize <= 0 means DefaultBatchSize. tpm/record may be nil.
func newTestClient(fake func(ctx context.Context, texts []string, taskType string) ([][]float32, error), batchSize int, tpm *budget.RateLimiter, record func(int64)) *Client {
	bs := batchSize
	if bs <= 0 {
		bs = DefaultBatchSize
	}
	c := &Client{
		model:     "test-model",
		batchSize: bs,
		tpm:       tpm,
		record:    record,
	}
	c.embed = func(ctx context.Context, _ string, texts []string, taskType string) ([][]float32, error) {
		return fake(ctx, texts, taskType)
	}
	return c
}
