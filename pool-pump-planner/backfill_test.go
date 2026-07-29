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

func TestFixedWindowSchedule(t *testing.T) {
	tz, _ := time.LoadLocation("Europe/Stockholm")
	// 96 slots of 15m starting at 2026-04-20 00:00 UTC = 02:00 local (DST).
	start := time.Date(2026, 4, 20, 0, 0, 0, 0, time.UTC)
	slots := make([]time.Time, 96)
	for i := range slots {
		slots[i] = start.Add(time.Duration(i*15) * time.Minute)
	}
	// Window = local hours [12,13]. That's 2 hours = 8 slots. In UTC that's
	// 10:00-12:00 (CEST=UTC+2), starting at slots[40].
	got := fixedWindowSchedule(slots, []int{12, 13}, tz)
	if len(got) != 96 {
		t.Fatalf("expected 96 slots, got %d", len(got))
	}
	ones := 0
	for i, v := range got {
		if v != 0 && v != 1 {
			t.Errorf("slot %d has non-binary value %d", i, v)
		}
		ones += v
	}
	if ones != 8 {
		t.Errorf("expected 8 on-slots for window [12,13], got %d", ones)
	}
	// Slots 40..47 (10:00-12:00 UTC = 12:00-14:00 local) should all be 1.
	for i := 40; i < 48; i++ {
		if got[i] != 1 {
			t.Errorf("expected slot %d (local %02dh) = 1, got 0",
				i, slots[i].In(tz).Hour())
		}
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
