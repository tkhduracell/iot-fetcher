package embed

import (
	"context"
	"errors"
	"reflect"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/budget"
)

func TestEstimateTokens(t *testing.T) {
	t.Parallel()

	cases := []struct {
		name  string
		in    []string
		wantT int64
	}{
		{"empty slice", nil, 0},
		{"empty string", []string{""}, 0},
		// 4 ASCII runes / 4 = 1 token.
		{"four ascii runes", []string{"abcd"}, 1},
		// 8 ASCII runes / 4 = 2 tokens.
		{"eight ascii runes", []string{"abcdefgh"}, 2},
		// runes/4 truncates: 7 runes -> 1 token.
		{"seven ascii truncates", []string{"abcdefg"}, 1},
		// Multi-byte rune counts as one rune: "héllo" = 5 runes = 1 token.
		{"multibyte counts as rune", []string{"héllo"}, 1},
		// Sum across batch: 12 runes / 4 = 3 tokens.
		{"sum across batch", []string{"abcd", "efgh", "ijkl"}, 3},
	}

	for _, tc := range cases {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			got := estimateTokens(tc.in)
			if got != tc.wantT {
				t.Fatalf("estimateTokens(%v) = %d, want %d", tc.in, got, tc.wantT)
			}
		})
	}
}

func TestSplitBatches(t *testing.T) {
	t.Parallel()

	cases := []struct {
		name string
		in   []string
		size int
		want [][]string
	}{
		{"empty", nil, 10, nil},
		{"empty explicit", []string{}, 10, nil},
		{"one under size", []string{"a"}, 5, [][]string{{"a"}}},
		{"exactly size", []string{"a", "b", "c"}, 3, [][]string{{"a", "b", "c"}}},
		{"one over size", []string{"a", "b", "c", "d"}, 3, [][]string{{"a", "b", "c"}, {"d"}}},
		{"multiple full batches", []string{"a", "b", "c", "d"}, 2, [][]string{{"a", "b"}, {"c", "d"}}},
		{"ragged last batch", []string{"a", "b", "c", "d", "e"}, 2, [][]string{{"a", "b"}, {"c", "d"}, {"e"}}},
		// size<=0 collapses to a single batch.
		{"size zero", []string{"a", "b"}, 0, [][]string{{"a", "b"}}},
		{"size negative", []string{"a", "b"}, -5, [][]string{{"a", "b"}}},
	}

	for _, tc := range cases {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			got := splitBatches(tc.in, tc.size)
			if !reflect.DeepEqual(got, tc.want) {
				t.Fatalf("splitBatches(%v, %d) = %v, want %v", tc.in, tc.size, got, tc.want)
			}
		})
	}
}

// TestEmbedBatch_EmptyInput verifies the short-circuit contract: no API call,
// no accounting.
func TestEmbedBatch_EmptyInput(t *testing.T) {
	t.Parallel()

	var called atomic.Int32
	var recorded atomic.Int64
	c := newTestClient(func(_ context.Context, _ []string, _ string) ([][]float32, error) {
		called.Add(1)
		return nil, nil
	}, 0, nil, func(n int64) { recorded.Add(n) })

	for _, in := range [][]string{nil, {}} {
		got, err := c.EmbedBatch(context.Background(), in)
		if err != nil {
			t.Fatalf("EmbedBatch(%v) err: %v", in, err)
		}
		if len(got) != 0 {
			t.Fatalf("EmbedBatch(%v) = %v, want empty", in, got)
		}
	}

	if called.Load() != 0 {
		t.Fatalf("embed fn called %d times for empty input; want 0", called.Load())
	}
	if recorded.Load() != 0 {
		t.Fatalf("recorded %d tokens for empty input; want 0", recorded.Load())
	}
}

// TestEmbedBatch_SubBatching exercises the order-preserving split: 5 texts
// with BatchSize=2 should produce 3 sub-batches and return 5 vectors in the
// original order.
func TestEmbedBatch_SubBatching(t *testing.T) {
	t.Parallel()

	var mu sync.Mutex
	var subBatches [][]string

	fake := func(_ context.Context, texts []string, task string) ([][]float32, error) {
		if task != taskRetrievalDocument {
			t.Errorf("unexpected task type %q", task)
		}
		mu.Lock()
		cp := append([]string(nil), texts...)
		subBatches = append(subBatches, cp)
		mu.Unlock()
		out := make([][]float32, len(texts))
		for i, s := range texts {
			// Encode the text length into the vector so we can verify
			// ordering after concatenation.
			out[i] = []float32{float32(len(s))}
		}
		return out, nil
	}

	var recorded int64
	c := newTestClient(fake, 2, nil, func(n int64) { recorded += n })

	in := []string{"a", "bb", "ccc", "dddd", "eeeee"}
	got, err := c.EmbedBatch(context.Background(), in)
	if err != nil {
		t.Fatalf("EmbedBatch: %v", err)
	}
	if len(got) != len(in) {
		t.Fatalf("got %d vectors, want %d", len(got), len(in))
	}
	for i, s := range in {
		if got[i][0] != float32(len(s)) {
			t.Fatalf("vector[%d] = %v; want marker %d for %q", i, got[i], len(s), s)
		}
	}

	wantSubs := [][]string{{"a", "bb"}, {"ccc", "dddd"}, {"eeeee"}}
	if !reflect.DeepEqual(subBatches, wantSubs) {
		t.Fatalf("sub-batches = %v; want %v", subBatches, wantSubs)
	}

	// 1+2+3+4+5 = 15 runes / 4 = 3 tokens (integer truncation). All
	// sub-batches together: (1+2)/4 + (3+4)/4 + 5/4 = 0 + 1 + 1 = 2.
	// Each batch records independently, so verify the sum matches that.
	wantTokens := int64(1+2)/4 + int64(3+4)/4 + int64(5)/4
	if recorded != wantTokens {
		t.Fatalf("recorded %d tokens; want %d", recorded, wantTokens)
	}
}

// TestEmbedBatch_RecordOnSuccessOnly verifies that a failed sub-batch does not
// call RecordTokens, and the error propagates.
func TestEmbedBatch_RecordOnSuccessOnly(t *testing.T) {
	t.Parallel()

	boom := errors.New("gemini unavailable")
	var calls atomic.Int32
	fake := func(_ context.Context, texts []string, _ string) ([][]float32, error) {
		n := calls.Add(1)
		if n == 2 {
			return nil, boom
		}
		out := make([][]float32, len(texts))
		for i := range out {
			out[i] = []float32{1, 2, 3}
		}
		return out, nil
	}

	var recorded int64
	c := newTestClient(fake, 1, nil, func(n int64) { recorded += n })

	// 3 sub-batches; second one fails.
	_, err := c.EmbedBatch(context.Background(), []string{"aaaa", "bbbb", "cccc"})
	if !errors.Is(err, boom) {
		t.Fatalf("EmbedBatch err = %v; want wraps %v", err, boom)
	}
	// Only the first sub-batch succeeded; "aaaa" = 4 runes / 4 = 1 token.
	if recorded != 1 {
		t.Fatalf("recorded %d tokens; want 1 (only first sub-batch)", recorded)
	}
	if calls.Load() != 2 {
		t.Fatalf("embed fn called %d times; want 2 (stopped after failure)", calls.Load())
	}
}

// TestEmbedBatch_TPMWaitIsEnforced verifies that the TPM limiter blocks
// between calls when the cost exceeds the refill rate.
func TestEmbedBatch_TPMWaitIsEnforced(t *testing.T) {
	t.Parallel()

	// Burst and rate = 4 tokens/sec. Each "abcd" batch costs 1 token, so
	// three back-to-back batches should be throttled to roughly nothing
	// at burst=4. But if we drain the bucket first by costing 4 tokens
	// per batch and running 2 batches, the second must wait ~1s.
	lim := budget.NewRateLimiter(4, 4)

	// Each text is 16 runes => 16/4 = 4 tokens, so a batch of 1 text = 4 tokens.
	// BatchSize=1 so each text is its own sub-batch.
	text := "abcdefghijklmnop" // 16 runes
	fake := func(_ context.Context, texts []string, _ string) ([][]float32, error) {
		out := make([][]float32, len(texts))
		for i := range out {
			out[i] = []float32{0}
		}
		return out, nil
	}
	c := newTestClient(fake, 1, lim, nil)

	start := time.Now()
	if _, err := c.EmbedBatch(context.Background(), []string{text, text}); err != nil {
		t.Fatalf("EmbedBatch: %v", err)
	}
	elapsed := time.Since(start)

	// First call drains 4 tokens instantly. Second call needs 4 tokens,
	// refill rate 4/s => ~1s wait. Allow generous slack for CI.
	if elapsed < 700*time.Millisecond {
		t.Fatalf("EmbedBatch completed in %v; expected >=700ms due to TPM throttling", elapsed)
	}
	if elapsed > 5*time.Second {
		t.Fatalf("EmbedBatch took %v; expected <5s (runaway wait)", elapsed)
	}
}

// TestEmbedBatch_TPMWaitRespectsContext verifies that a cancelled context
// aborts the TPM wait with the context error.
func TestEmbedBatch_TPMWaitRespectsContext(t *testing.T) {
	t.Parallel()

	// Rate 1 token/sec, burst 1. Second call would need a full second of wait.
	lim := budget.NewRateLimiter(1, 1)

	fake := func(_ context.Context, texts []string, _ string) ([][]float32, error) {
		out := make([][]float32, len(texts))
		for i := range out {
			out[i] = []float32{0}
		}
		return out, nil
	}
	c := newTestClient(fake, 1, lim, nil)

	// Prime the limiter: drain it.
	if _, err := c.EmbedBatch(context.Background(), []string{"abcd"}); err != nil {
		t.Fatalf("prime: %v", err)
	}

	ctx, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer cancel()

	_, err := c.EmbedBatch(ctx, []string{"abcd"})
	if err == nil {
		t.Fatal("expected context error, got nil")
	}
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("err = %v; want wraps context.DeadlineExceeded", err)
	}
}

// TestEmbedQuery_UsesRetrievalQuery verifies the task-type split.
func TestEmbedQuery_UsesRetrievalQuery(t *testing.T) {
	t.Parallel()

	var gotTask string
	fake := func(_ context.Context, texts []string, task string) ([][]float32, error) {
		gotTask = task
		out := make([][]float32, len(texts))
		for i := range out {
			out[i] = []float32{7}
		}
		return out, nil
	}
	c := newTestClient(fake, 0, nil, nil)

	vec, err := c.EmbedQuery(context.Background(), "find me docs")
	if err != nil {
		t.Fatalf("EmbedQuery: %v", err)
	}
	if gotTask != taskRetrievalQuery {
		t.Fatalf("task = %q; want %q", gotTask, taskRetrievalQuery)
	}
	if len(vec) != 1 || vec[0] != 7 {
		t.Fatalf("vec = %v; want [7]", vec)
	}
}

// TestEmbed_SingleText is the Embed() convenience wrapper smoke test.
func TestEmbed_SingleText(t *testing.T) {
	t.Parallel()

	fake := func(_ context.Context, texts []string, task string) ([][]float32, error) {
		if task != taskRetrievalDocument {
			t.Errorf("task = %q; want RETRIEVAL_DOCUMENT", task)
		}
		if len(texts) != 1 {
			t.Errorf("batch of %d; want 1", len(texts))
		}
		return [][]float32{{1, 2, 3}}, nil
	}
	c := newTestClient(fake, 0, nil, nil)

	vec, err := c.Embed(context.Background(), "hello")
	if err != nil {
		t.Fatalf("Embed: %v", err)
	}
	if !reflect.DeepEqual(vec, []float32{1, 2, 3}) {
		t.Fatalf("vec = %v; want [1 2 3]", vec)
	}
}

// TestEmbedBatch_CountMismatch ensures we fail loudly if Gemini returns
// a different number of vectors than we asked for.
func TestEmbedBatch_CountMismatch(t *testing.T) {
	t.Parallel()

	fake := func(_ context.Context, _ []string, _ string) ([][]float32, error) {
		return [][]float32{{1}}, nil // only 1 vector, but we'll send 2 texts
	}
	c := newTestClient(fake, 0, nil, nil)

	_, err := c.EmbedBatch(context.Background(), []string{"a", "b"})
	if err == nil {
		t.Fatal("expected count-mismatch error, got nil")
	}
}

// TestNewClient_RequiresAPIKey verifies Config validation.
func TestNewClient_RequiresAPIKey(t *testing.T) {
	t.Parallel()

	if _, err := NewClient(context.Background(), Config{Model: "x"}); err == nil {
		t.Fatal("expected error for missing APIKey")
	}
	if _, err := NewClient(context.Background(), Config{APIKey: "x"}); err == nil {
		t.Fatal("expected error for missing Model")
	}
}
