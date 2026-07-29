package main

import (
	"math"
	"testing"
	"time"
)

func TestSamplesToKWhPerSlot(t *testing.T) {
	start := time.Unix(1700000000, 0).UTC()
	slotMinutes := 15
	slots := []time.Time{
		start,
		start.Add(15 * time.Minute),
		start.Add(30 * time.Minute),
		start.Add(45 * time.Minute),
	}
	// Samples: slot 0 = 2 kW, slot 1 = 0.5 kW, slot 2 = (no data), slot 3 = 1 kW.
	samples := []promSample{
		{Timestamp: float64(start.Unix()), Value: 2.0},
		{Timestamp: float64(start.Add(15 * time.Minute).Unix()), Value: 0.5},
		{Timestamp: float64(start.Add(45 * time.Minute).Unix()), Value: 1.0},
	}
	got := samplesToKWhPerSlot(samples, slots, slotMinutes)
	want := []float64{0.5, 0.125, 0, 0.25}
	if len(got) != len(want) {
		t.Fatalf("len mismatch: got %d want %d", len(got), len(want))
	}
	for i := range want {
		if math.Abs(got[i]-want[i]) > 1e-9 {
			t.Errorf("slot %d: got %.4f want %.4f", i, got[i], want[i])
		}
	}
}
