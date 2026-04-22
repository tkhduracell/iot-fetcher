package extract

import (
	"context"
	"errors"
	"sync/atomic"
	"testing"

	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/budget"
)

// TestFlash_DailyBudgetGate asserts the daily-budget check fires before the
// RPM limiter or any API call. Using an empty FlashClient is safe because the
// gate returns early — the nil genai.Client is never touched.
func TestFlash_DailyBudgetGate(t *testing.T) {
	var current int64 = 100
	var incCalls int64
	counter := &budget.DailyCounter{
		Cap:     100,
		Current: func() int64 { return atomic.LoadInt64(&current) },
		Increment: func(n int64) {
			atomic.AddInt64(&incCalls, n)
			atomic.AddInt64(&current, n)
		},
	}
	// RPM limiter that would panic if we actually hit it (cost > burst).
	rl := budget.NewRateLimiter(0, 0) // unlimited — fine even if reached
	fc := &FlashClient{
		model:      "gemini-2.5-flash-lite",
		prompt:     defaultFlashPrompt,
		rpmLimiter: rl,
		daily:      counter,
	}

	_, err := fc.ExtractBytes(context.Background(), "hint", "application/pdf", []byte("dummy"))
	if !errors.Is(err, budget.ErrDailyBudgetExhausted) {
		t.Fatalf("expected ErrDailyBudgetExhausted, got %v", err)
	}
	// Increment must NOT have been called — failed requests aren't charged.
	if got := atomic.LoadInt64(&incCalls); got != 0 {
		t.Fatalf("Increment called %d times on budget-exhausted path, want 0", got)
	}
}

// TestFlash_EmptyBodyRejected asserts we don't waste a Files upload on zero bytes.
func TestFlash_EmptyBodyRejected(t *testing.T) {
	fc := &FlashClient{}
	_, err := fc.ExtractBytes(context.Background(), "hint", "application/pdf", nil)
	if err == nil {
		t.Fatal("expected error on empty body")
	}
}

// TestFlash_NilReceiverRejected guards against accidental *FlashClient=nil
// usage inside Router.
func TestFlash_NilReceiverRejected(t *testing.T) {
	var fc *FlashClient
	_, err := fc.ExtractBytes(context.Background(), "hint", "application/pdf", []byte("x"))
	if err == nil {
		t.Fatal("expected error on nil receiver")
	}
}

// TestFlash_DailyCounterNilAllowed asserts a nil DailyCounter doesn't crash
// and doesn't block the request (budget accounting is optional).
// We stop the flow at the RPM limiter via a cancelled context so no real API
// call is made.
func TestFlash_NoDailyCounter_ContextCancellation(t *testing.T) {
	rl := budget.NewRateLimiter(1, 1)
	// Drain the single token so the next Wait blocks until refill.
	_ = rl.Wait(context.Background(), 1)

	fc := &FlashClient{
		model:      "gemini-2.5-flash-lite",
		prompt:     defaultFlashPrompt,
		rpmLimiter: rl,
		daily:      nil, // not configured
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()

	_, err := fc.ExtractBytes(ctx, "hint", "application/pdf", []byte("x"))
	if err == nil {
		t.Fatal("expected error (context cancelled)")
	}
}

func TestSplitPageRangesUsesDefaultWhenPerCallZero(t *testing.T) {
	got := splitPageRanges(5, 0)
	if len(got) != 1 || got[0] != "1-5" {
		t.Fatalf("splitPageRanges(5,0) = %v, want [1-5]", got)
	}
}
