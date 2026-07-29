package main

import (
	"fmt"
	"sort"
	"strings"
	"time"
)

// backfillResult is one row in the stdout summary table.
type backfillResult struct {
	Date              time.Time // site-local midnight of the anchor day
	Mode              string    // "optimal" / "fallback" / "ERR"
	Hours             float64
	TargetHours       int
	CostSEK           float64
	SlackHours        float64
	Missing           string // "none" if everything was present
	OnHours           []int  // unique local clock hours where any slot was on
	NightBaselineSEK  float64
	AfternoonBaseSEK  float64
	Err               error // non-nil for a day that completely failed
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
	fmt.Fprintf(&b, "  %-10s  %-9s  %5s  %3s  %10s  %10s  %10s  %5s  %-12s  ON_HOURS (local)\n",
		"DATE", "MODE", "HRS", "TGT", "OPT(SEK)", "NIGHT(SEK)", "AFTNN(SEK)", "SLACK", "MISSING")

	sorted := make([]backfillResult, len(results))
	copy(sorted, results)
	sort.Slice(sorted, func(i, j int) bool { return sorted[i].Date.After(sorted[j].Date) })

	totHours := 0.0
	totTgt := 0
	totCost := 0.0
	totNight := 0.0
	totAftnn := 0.0
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
		fmt.Fprintf(&b, "  %-10s  %-9s  %5.1f  %3d  %10.2f  %10.2f  %10.2f  %5.1f  %-12s  %s\n",
			date, r.Mode, r.Hours, r.TargetHours, r.CostSEK,
			r.NightBaselineSEK, r.AfternoonBaseSEK,
			r.SlackHours, ifEmpty(r.Missing, "-"), strings.Join(on, " "))
		totHours += r.Hours
		totTgt += r.TargetHours
		totCost += r.CostSEK
		totNight += r.NightBaselineSEK
		totAftnn += r.AfternoonBaseSEK
	}
	fmt.Fprintf(&b, "  %s\n", strings.Repeat("─", 115))
	succ := len(results) - failures
	if succ > 0 {
		fmt.Fprintf(&b, "  %-10s  %-9s  %5.1f  %3d  %10.2f  %10.2f  %10.2f   opt avg %.2f SEK/day\n",
			"Totals", "", totHours, totTgt, totCost, totNight, totAftnn, totCost/float64(succ))
		fmt.Fprintf(&b, "  Savings vs night-fixed:     %7.2f SEK (%5.1f%%)\n",
			totNight-totCost, pct(totNight-totCost, totNight))
		fmt.Fprintf(&b, "  Savings vs afternoon-fixed: %7.2f SEK (%5.1f%%)\n",
			totAftnn-totCost, pct(totAftnn-totCost, totAftnn))
	}
	fmt.Fprintf(&b, "  Failures: %d\n", failures)
	return b.String()
}

func pct(numer, denom float64) float64 {
	if denom == 0 {
		return 0
	}
	return 100 * numer / denom
}

func ifEmpty(s, def string) string {
	if s == "" || s == "none" {
		return def
	}
	return s
}

// fixedWindowSchedule returns a schedule of the same length as slots where
// slot i is 1 iff slots[i]'s local-clock hour is in windowHours.
func fixedWindowSchedule(slots []time.Time, windowHours []int, tz *time.Location) []int {
	set := map[int]bool{}
	for _, h := range windowHours {
		set[h] = true
	}
	out := make([]int, len(slots))
	for i, s := range slots {
		if set[s.In(tz).Hour()] {
			out[i] = 1
		}
	}
	return out
}

// runBaselines computes and writes two fixed-schedule "what if" baselines
// for comparison against the MILP optimum: a night window and an afternoon
// window. Uses the same slotCost function as the optimizer so costs are
// directly comparable. Emits under distinct run tags so Grafana can plot
// all three side by side. Returns (nightCost, afternoonCost) for the table.
func runBaselines(cfg *Config, in planInputs, anchorDate string, dryRun bool) (float64, float64) {
	night := writeBaseline(cfg, in, cfg.BaselineNightHours, "baseline_night", anchorDate, dryRun)
	afternoon := writeBaseline(cfg, in, cfg.BaselineAfternoonHours, "baseline_afternoon", anchorDate, dryRun)
	return night, afternoon
}

func writeBaseline(cfg *Config, in planInputs, windowHours []int, runTag, anchorDate string, dryRun bool) float64 {
	sch := fixedWindowSchedule(in.Slots, windowHours, cfg.Timezone)
	stats := fallbackStats(cfg, sch, in.Prices, in.Solar)
	tags := map[string]string{"run": runTag, "anchor_date": anchorDate}

	if dryRun {
		// Dry-run: skip VM writes but still return the computed cost for the
		// stdout table. writePlan respects cfg.DryRun to no-op, but also
		// prints the schedule which is noisy for baselines; skip it entirely.
		return stats.expectedCostSEK
	}
	if err := writePlan(cfg, in.Slots, sch, in.Prices, in.Solar, in.SolarRaw, stats,
		in.WaterTemp, in.WaterOK, len(windowHours), "baseline", "none", tags); err != nil {
		// Non-fatal: baselines are comparison only, don't fail the day.
		fmt.Printf("[backfill] baseline %s write failed: %v\n", runTag, err)
	}
	return stats.expectedCostSEK
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

func runBackfill(cfg *Config, days int, end time.Time, dryRun bool) error {
	if days <= 0 {
		return fmt.Errorf("--days must be >= 1, got %d", days)
	}
	cfg.Backfill = true

	planHH, planMM, err := parseHHMM(cfg.PlanTime)
	if err != nil {
		return fmt.Errorf("POOL_PLAN_TIME parse: %w", err)
	}

	dates := backfillDates(end, days, cfg.Timezone)
	results := make([]backfillResult, 0, len(dates))

	for _, d := range dates {
		anchorLocal := time.Date(d.Year(), d.Month(), d.Day(), planHH, planMM, 0, 0, cfg.Timezone)
		tags := map[string]string{
			"run":         "backfill",
			"anchor_date": d.Format("2006-01-02"),
		}

		origHost, origToken := "", ""
		if dryRun {
			// Blank out credentials so WritePoints no-ops (it checks both
			// and logs-and-returns). Cheaper than threading a flag through.
			origHost, origToken = cfg.InfluxHost, cfg.InfluxToken
			cfg.InfluxHost, cfg.InfluxToken = "", ""
		}
		report, inputs, planErr := plan(cfg, anchorLocal.UTC(), tags)
		if dryRun {
			cfg.InfluxHost, cfg.InfluxToken = origHost, origToken
		}

		if planErr != nil {
			results = append(results, backfillResult{Date: d, Mode: "ERR", Err: planErr})
			continue
		}

		nightCost, afternoonCost := runBaselines(cfg, inputs, tags["anchor_date"], dryRun)

		results = append(results, backfillResult{
			Date:              d,
			Mode:              report.Mode,
			Hours:             report.Hours,
			TargetHours:       report.TargetHours,
			CostSEK:           report.CostSEK,
			SlackHours:        report.SlackHours,
			Missing:           report.Missing,
			OnHours:           report.OnHours,
			NightBaselineSEK:  nightCost,
			AfternoonBaseSEK:  afternoonCost,
		})
	}

	fmt.Print(formatBackfillTable(results, end, cfg.Timezone, cfg.PlanTime))

	anyOK := false
	for _, r := range results {
		if r.Err == nil {
			anyOK = true
			break
		}
	}
	if !anyOK {
		return fmt.Errorf("all %d backfill days failed", len(results))
	}
	return nil
}
