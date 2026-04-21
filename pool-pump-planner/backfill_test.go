package main

import (
	"strings"
	"testing"
	"time"
)

func TestBackfillDates(t *testing.T) {
	tz, _ := time.LoadLocation("Europe/Stockholm")
	end := time.Date(2026, 4, 20, 0, 0, 0, 0, tz)
	got := backfillDates(end, 3, tz)
	want := []time.Time{
		time.Date(2026, 4, 18, 0, 0, 0, 0, tz),
		time.Date(2026, 4, 19, 0, 0, 0, 0, tz),
		time.Date(2026, 4, 20, 0, 0, 0, 0, tz),
	}
	if len(got) != len(want) {
		t.Fatalf("len got=%d want=%d", len(got), len(want))
	}
	for i := range want {
		if !got[i].Equal(want[i]) {
			t.Errorf("[%d] got=%s want=%s", i, got[i], want[i])
		}
	}
}

func TestBackfillDatesZeroDays(t *testing.T) {
	tz := time.UTC
	end := time.Date(2026, 4, 20, 0, 0, 0, 0, tz)
	got := backfillDates(end, 0, tz)
	if len(got) != 0 {
		t.Fatalf("expected empty slice, got %v", got)
	}
}

func TestFormatBackfillTable(t *testing.T) {
	tz, _ := time.LoadLocation("Europe/Stockholm")
	end := time.Date(2026, 4, 20, 0, 0, 0, 0, tz)
	results := []backfillResult{
		{
			Date: time.Date(2026, 4, 19, 0, 0, 0, 0, tz),
			Mode: "optimal", Hours: 6.0, TargetHours: 6, CostSEK: 9.81, SlackHours: 0.0,
			Missing: "none", OnHours: []int{1, 2, 3, 4, 13, 14},
		},
		{
			Date: time.Date(2026, 4, 20, 0, 0, 0, 0, tz),
			Mode: "fallback", Hours: 8.0, TargetHours: 8, CostSEK: 18.40, SlackHours: 0.0,
			Missing: "prices", OnHours: []int{1, 2, 3, 4, 12, 13, 14, 15},
		},
	}
	out := formatBackfillTable(results, end, tz, "14:05")
	if !strings.Contains(out, "2026-04-20") {
		t.Errorf("expected 2026-04-20 row, got:\n%s", out)
	}
	if !strings.Contains(out, "prices") {
		t.Errorf("expected 'prices' in missing col, got:\n%s", out)
	}
	pos20 := strings.Index(out, "2026-04-20")
	pos19 := strings.Index(out, "2026-04-19")
	if pos19 < pos20 || pos20 < 0 {
		t.Errorf("expected 04-20 before 04-19 (newest first); positions 04-20=%d 04-19=%d\n%s",
			pos20, pos19, out)
	}
	if !strings.Contains(out, "Failures: 0") {
		t.Errorf("expected 'Failures: 0', got:\n%s", out)
	}
}
