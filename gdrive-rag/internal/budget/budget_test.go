package budget

import (
	"context"
	"errors"
	"sync/atomic"
	"testing"
	"time"
)

func TestRateLimiterImmediate(t *testing.T) {
	// 100 tokens/sec, burst 100 -> first 100 cost-1 calls are free.
	rl := NewRateLimiter(100, 100)
	ctx := context.Background()
	start := time.Now()
	for i := 0; i < 100; i++ {
		if err := rl.Wait(ctx, 1); err != nil {
			t.Fatalf("Wait #%d: %v", i, err)
		}
	}
	if d := time.Since(start); d > 50*time.Millisecond {
		t.Errorf("100 free tokens took %v; expected <50ms", d)
	}
}

func TestRateLimiterBlocks(t *testing.T) {
	// 100 tokens/sec, burst 10. After draining burst, next token takes ~10ms.
	rl := NewRateLimiter(100, 10)
	ctx := context.Background()
	for i := 0; i < 10; i++ {
		if err := rl.Wait(ctx, 1); err != nil {
			t.Fatalf("burst drain: %v", err)
		}
	}
	start := time.Now()
	if err := rl.Wait(ctx, 1); err != nil {
		t.Fatalf("Wait over cap: %v", err)
	}
	d := time.Since(start)
	if d < 5*time.Millisecond {
		t.Errorf("Wait returned too fast (%v); limiter not blocking", d)
	}
	if d > 200*time.Millisecond {
		t.Errorf("Wait slept %v; expected ~10ms", d)
	}
}

func TestRateLimiterCtxCancel(t *testing.T) {
	rl := NewRateLimiter(1, 1) // 1 token/sec
	// Drain.
	_ = rl.Wait(context.Background(), 1)

	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer cancel()
	err := rl.Wait(ctx, 1)
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Errorf("expected DeadlineExceeded, got %v", err)
	}
}

func TestRateLimiterCostTooLarge(t *testing.T) {
	rl := NewRateLimiter(100, 10)
	err := rl.Wait(context.Background(), 11)
	if err == nil {
		t.Errorf("expected error for cost > burst")
	}
}

func TestRateLimiterUnlimited(t *testing.T) {
	rl := NewRateLimiter(0, 0) // rate <= 0 means unlimited
	start := time.Now()
	for i := 0; i < 1000; i++ {
		if err := rl.Wait(context.Background(), 10); err != nil {
			t.Fatalf("unlimited Wait err: %v", err)
		}
	}
	if time.Since(start) > 10*time.Millisecond {
		t.Errorf("unlimited limiter blocked")
	}
}

func TestDailyCounterCheck(t *testing.T) {
	var cur int64 = 40
	d := &DailyCounter{
		Cap:       100,
		Current:   func() int64 { return cur },
		Increment: func(n int64) { cur += n },
	}

	if err := d.Check(50); err != nil {
		t.Errorf("under cap: got %v", err)
	}
	// Exactly at cap (40+60=100) is still OK (Current+cost > Cap is the rule).
	if err := d.Check(60); err != nil {
		t.Errorf("at cap: got %v", err)
	}
	// 40+61=101 exceeds cap.
	if err := d.Check(61); !errors.Is(err, ErrDailyBudgetExhausted) {
		t.Errorf("over cap: want ErrDailyBudgetExhausted, got %v", err)
	}
}

func TestDailyCounterAdd(t *testing.T) {
	var called int64
	d := &DailyCounter{
		Cap:       1000,
		Current:   func() int64 { return atomic.LoadInt64(&called) },
		Increment: func(n int64) { atomic.AddInt64(&called, n) },
	}
	d.Add(7)
	d.Add(3)
	if got := atomic.LoadInt64(&called); got != 10 {
		t.Errorf("Increment total: got %d want 10", got)
	}
}

func TestDailyCounterNoCap(t *testing.T) {
	d := &DailyCounter{Cap: 0, Current: func() int64 { return 1 << 30 }}
	if err := d.Check(1 << 30); err != nil {
		t.Errorf("no cap: got %v", err)
	}
}

func TestDailyCounterNilCurrent(t *testing.T) {
	d := &DailyCounter{Cap: 10}
	if err := d.Check(5); err != nil {
		t.Errorf("nil Current: got %v", err)
	}
	d.Add(5) // must not panic when Increment is nil
}
