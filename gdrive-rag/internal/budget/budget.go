package budget

import (
	"context"
	"errors"
	"fmt"
	"sync"
	"time"
)

// ErrDailyBudgetExhausted is returned by DailyCounter.Check when adding the
// requested cost would exceed the daily cap. Callers (embed, extract) should
// finish in-flight work and sleep until the counter rolls over at 00:05 PT.
var ErrDailyBudgetExhausted = errors.New("daily budget exhausted")

// RateLimiter is a minimal token-bucket rate limiter. Tokens refill
// continuously at Rate per second, capped at Burst.
//
// Wait blocks until `cost` tokens are available or the context is cancelled.
// It is safe for concurrent use.
//
// We roll our own instead of pulling in golang.org/x/time/rate because that
// dep is not currently in go.sum and the Task 2 constraint forbids modifying
// go.mod/go.sum.
type RateLimiter struct {
	mu     sync.Mutex
	rate   float64 // tokens per second
	burst  float64 // bucket capacity
	tokens float64
	last   time.Time
	now    func() time.Time // clock; defaults to time.Now
}

// NewRateLimiter builds a limiter that refills `ratePerSec` tokens/second and
// caps the bucket at `burst`. The bucket starts full.
//
// ratePerSec <= 0 means "unlimited"; Wait always returns immediately.
func NewRateLimiter(ratePerSec, burst float64) *RateLimiter {
	rl := &RateLimiter{
		rate:  ratePerSec,
		burst: burst,
		now:   time.Now,
	}
	rl.tokens = burst
	rl.last = time.Now()
	return rl
}

// SetNow overrides the clock. For tests.
func (r *RateLimiter) SetNow(now func() time.Time) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.now = now
	r.last = now()
}

// Wait blocks until `cost` tokens are available, deducting them before return.
// Returns an error if ctx is cancelled or `cost` > burst.
func (r *RateLimiter) Wait(ctx context.Context, cost int) error {
	if cost <= 0 {
		return nil
	}
	if r.rate <= 0 {
		// Unlimited.
		return nil
	}
	costF := float64(cost)
	if costF > r.burst {
		return fmt.Errorf("rate limiter: cost %d exceeds burst %v", cost, r.burst)
	}

	for {
		r.mu.Lock()
		r.refillLocked()
		if r.tokens >= costF {
			r.tokens -= costF
			r.mu.Unlock()
			return nil
		}
		need := costF - r.tokens
		waitSec := need / r.rate
		r.mu.Unlock()

		// Sleep for waitSec or until ctx is cancelled.
		timer := time.NewTimer(time.Duration(waitSec * float64(time.Second)))
		select {
		case <-ctx.Done():
			timer.Stop()
			return ctx.Err()
		case <-timer.C:
		}
	}
}

// refillLocked adds tokens based on elapsed wall time. Caller holds r.mu.
func (r *RateLimiter) refillLocked() {
	now := r.now()
	elapsed := now.Sub(r.last).Seconds()
	if elapsed <= 0 {
		return
	}
	r.tokens += elapsed * r.rate
	if r.tokens > r.burst {
		r.tokens = r.burst
	}
	r.last = now
}

// DailyCounter wraps a persistent per-day usage counter with a cap.
// It is deliberately decoupled from the state package: the caller wires
// the Current/Increment callbacks to its own persistence layer so budget
// doesn't need to know the state file format.
//
// Cap <= 0 means "no hard cap"; Check always returns nil.
type DailyCounter struct {
	Cap       int64
	Current   func() int64
	Increment func(n int64)
}

// Check reports whether charging `cost` against the counter would exceed Cap.
// Returns ErrDailyBudgetExhausted if so. Thread-safety is the caller's
// responsibility via the Current/Increment callbacks.
func (d *DailyCounter) Check(cost int64) error {
	if d.Cap <= 0 {
		return nil
	}
	if d.Current == nil {
		return nil
	}
	if d.Current()+cost > d.Cap {
		return ErrDailyBudgetExhausted
	}
	return nil
}

// Add charges `n` against the counter via the Increment callback.
// No-op if Increment is nil.
func (d *DailyCounter) Add(n int64) {
	if d.Increment == nil {
		return
	}
	d.Increment(n)
}
