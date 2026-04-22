package sync

import (
	"testing"
	"time"
)

func mustLoadPT(t *testing.T) *time.Location {
	t.Helper()
	loc, err := time.LoadLocation("America/Los_Angeles")
	if err != nil {
		t.Fatalf("load America/Los_Angeles: %v", err)
	}
	return loc
}

func TestNextBudgetReset(t *testing.T) {
	pt := mustLoadPT(t)

	tests := []struct {
		name string
		// now is expressed in Pacific for clarity.
		now  time.Time
		want time.Time
	}{
		{
			name: "late evening rolls to next-day 00:05",
			now:  time.Date(2025, 6, 10, 23, 0, 0, 0, pt),
			want: time.Date(2025, 6, 11, 0, 5, 0, 0, pt),
		},
		{
			name: "00:04 rolls to 00:05 same day",
			now:  time.Date(2025, 6, 10, 0, 4, 0, 0, pt),
			want: time.Date(2025, 6, 10, 0, 5, 0, 0, pt),
		},
		{
			name: "exactly 00:05 rolls to next day",
			now:  time.Date(2025, 6, 10, 0, 5, 0, 0, pt),
			want: time.Date(2025, 6, 11, 0, 5, 0, 0, pt),
		},
		{
			name: "01:00 rolls to next-day 00:05",
			now:  time.Date(2025, 6, 10, 1, 0, 0, 0, pt),
			want: time.Date(2025, 6, 11, 0, 5, 0, 0, pt),
		},
		{
			name: "day boundary: 23:59 PT Dec 31 rolls to Jan 1 00:05",
			now:  time.Date(2025, 12, 31, 23, 59, 0, 0, pt),
			want: time.Date(2026, 1, 1, 0, 5, 0, 0, pt),
		},
		{
			name: "UTC-expressed now is converted to PT correctly",
			// 2025-06-10 06:00 UTC == 2025-06-09 23:00 PDT. Expected reset is
			// 2025-06-10 00:05 PDT.
			now:  time.Date(2025, 6, 10, 6, 0, 0, 0, time.UTC),
			want: time.Date(2025, 6, 10, 0, 5, 0, 0, pt),
		},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			got := nextBudgetReset(tc.now)
			if !got.Equal(tc.want) {
				t.Errorf("nextBudgetReset(%s) = %s; want %s",
					tc.now.Format(time.RFC3339),
					got.Format(time.RFC3339),
					tc.want.Format(time.RFC3339))
			}
			if !got.After(tc.now) {
				t.Errorf("nextBudgetReset(%s) = %s is not strictly after now",
					tc.now.Format(time.RFC3339),
					got.Format(time.RFC3339))
			}
		})
	}
}
