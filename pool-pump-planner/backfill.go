package main

import (
	"fmt"
	"sort"
	"strings"
	"time"
)

// backfillResult is one row in the stdout summary table.
type backfillResult struct {
	Date        time.Time // site-local midnight of the anchor day
	Mode        string    // "optimal" / "fallback" / "ERR"
	Hours       float64
	TargetHours int
	CostSEK     float64
	SlackHours  float64
	Missing     string // "none" if everything was present
	OnHours     []int  // unique local clock hours where any slot was on
	Err         error  // non-nil for a day that completely failed
}

// backfillDates returns `days` calendar-day midnights in the given tz, oldest
// first, ending on `end` (inclusive). `end` is truncated to the day in tz.
func backfillDates(end time.Time, days int, tz *time.Location) []time.Time {
	if days <= 0 {
		return nil
	}
	end = end.In(tz)
	endDay := time.Date(end.Year(), end.Month(), end.Day(), 0, 0, 0, 0, tz)
	out := make([]time.Time, 0, days)
	for i := days - 1; i >= 0; i-- {
		out = append(out, endDay.AddDate(0, 0, -i))
	}
	return out
}

// formatBackfillTable renders the result slice as a stdout-ready table,
// newest first.
func formatBackfillTable(results []backfillResult, end time.Time, tz *time.Location, planTime string) string {
	var b strings.Builder
	fmt.Fprintf(&b, "Pool pump backfill — %d days ending %s (anchor %s %s)\n\n",
		len(results), end.In(tz).Format("2006-01-02"), planTime, tz.String())
	fmt.Fprintf(&b, "  %-10s  %-9s  %5s  %3s  %10s  %5s  %-12s  ON_HOURS (local)\n",
		"DATE", "MODE", "HRS", "TGT", "COST(SEK)", "SLACK", "MISSING")

	sorted := make([]backfillResult, len(results))
	copy(sorted, results)
	sort.Slice(sorted, func(i, j int) bool { return sorted[i].Date.After(sorted[j].Date) })

	totHours := 0.0
	totTgt := 0
	totCost := 0.0
	failures := 0
	for _, r := range sorted {
		date := r.Date.Format("2006-01-02")
		if r.Err != nil {
			fmt.Fprintf(&b, "  %-10s  %-9s  %s\n", date, "ERR", r.Err.Error())
			failures++
			continue
		}
		on := make([]string, 0, len(r.OnHours))
		for _, h := range r.OnHours {
			on = append(on, fmt.Sprintf("%02d", h))
		}
		fmt.Fprintf(&b, "  %-10s  %-9s  %5.1f  %3d  %10.2f  %5.1f  %-12s  %s\n",
			date, r.Mode, r.Hours, r.TargetHours, r.CostSEK, r.SlackHours,
			ifEmpty(r.Missing, "-"), strings.Join(on, " "))
		totHours += r.Hours
		totTgt += r.TargetHours
		totCost += r.CostSEK
	}
	fmt.Fprintf(&b, "  %s\n", strings.Repeat("─", 90))
	succ := len(results) - failures
	if succ > 0 {
		fmt.Fprintf(&b, "  %-10s  %-9s  %5.1f  %3d  %10.2f   avg/day %.2fh, %.2f SEK\n",
			"Totals", "", totHours, totTgt, totCost, totHours/float64(succ), totCost/float64(succ))
	}
	fmt.Fprintf(&b, "  Failures: %d\n", failures)
	return b.String()
}

func ifEmpty(s, def string) string {
	if s == "" || s == "none" {
		return def
	}
	return s
}

// onHoursFromSchedule returns the sorted unique local clock hours in which at
// least one slot is 1.
func onHoursFromSchedule(sch []int, slots []time.Time, tz *time.Location) []int {
	seen := map[int]bool{}
	for i, v := range sch {
		if v == 1 {
			seen[slots[i].In(tz).Hour()] = true
		}
	}
	out := make([]int, 0, len(seen))
	for h := range seen {
		out = append(out, h)
	}
	sort.Ints(out)
	return out
}
