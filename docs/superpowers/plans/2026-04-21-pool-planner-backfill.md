# Pool-pump planner — backfill subcommand — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `backfill` subcommand to `pool-pump-planner` that replays the planner for each of the last N days (default 30), anchored at each day's `POOL_PLAN_TIME`, writes tagged points to VM, and prints a stdout summary table.

**Architecture:** Small internal refactor to let `plan()` run at an arbitrary "now", plus a new `backfill.go` that owns the day loop and printer. Live runs gain a `run=live` tag; backfill runs carry `run=backfill` + `anchor_date=YYYY-MM-DD`. Historical solar comes from the real PV metric (`sigenergy_pv_power_power_kw{string="total"}`) aggregated per slot. The existing Grafana plan panel adds `{run="live"}` so backfill writes don't pollute it.

**Tech Stack:** Go 1.26, VictoriaMetrics/Prometheus API, InfluxDB v2 line protocol, Grafana SDK (TypeScript) for dashboard.

**Spec:** `docs/superpowers/specs/2026-04-21-pool-planner-backfill-design.md`

---

## File Structure

Each file has one clear responsibility:

| File | Responsibility | Change type |
|------|---------------|-------------|
| `pool-pump-planner/main.go` | Verb dispatch + scheduler loop | modify (tiny) |
| `pool-pump-planner/planner.go` | One-shot plan: fetch → solve → write | modify (signature + tag threading) |
| `pool-pump-planner/config.go` | Env-backed config struct | modify (add `Backfill bool`) |
| `pool-pump-planner/vm.go` | VictoriaMetrics PromQL client | modify (add `fetchWaterTempAt`, `fetchSolarHistoricalKWh`) |
| `pool-pump-planner/solar.go` | Solar kWh per slot — live forecast path | unchanged (rename nothing) |
| `pool-pump-planner/backfill.go` | Day loop + stdout table | **new** |
| `pool-pump-planner/backfill_test.go` | Unit tests for date math + formatter | **new** |
| `grafana/src/panels/pool.ts` | Grafana pool panel definition | modify (add `{run="live"}` to plan panel) |

---

## Task 1: Refactor `plan()` to accept `now` and `extraTags`; tag live runs

Foundation for everything else. Keeps existing behavior bit-for-bit when `extraTags` is `{"run":"live"}`.

**Files:**
- Modify: `pool-pump-planner/planner.go`
- Modify: `pool-pump-planner/main.go`

- [ ] **Step 1: Update `plan()` signature and body in `planner.go`**

Replace the current `plan` function (lines 28–64) with:

```go
func plan(cfg *Config, now time.Time, extraTags map[string]string) error {
	slotMinutes := cfg.SlotMinutes
	horizonSlots := cfg.HorizonSlots()

	now = now.UTC().Truncate(time.Hour)
	slots := make([]time.Time, horizonSlots)
	for i := 0; i < horizonSlots; i++ {
		slots[i] = now.Add(time.Duration(i*slotMinutes) * time.Minute)
	}

	prices := cfg.fetchHourlyPrices(slots)
	var solar []float64
	if cfg.Backfill {
		solar = cfg.fetchSolarHistoricalKWh(slots)
	} else {
		solar = cfg.fetchSolarForecast(slots)
	}
	waterTemp, waterOK := cfg.fetchWaterTempAt(now)

	missing := missingInputs(prices, waterOK, horizonSlots)
	if missing != "" {
		log.Printf("[planner] missing inputs %s, falling back to static schedule", missing)
		sch := fallbackSchedule(cfg, slots)
		stats := fallbackStats(cfg, sch, prices, solar)
		tgt := len(cfg.FallbackNightHours) + len(cfg.FallbackAfternoonHours)
		return writePlan(cfg, slots, sch, prices, solar, stats, waterTemp, waterOK, tgt, "fallback", missing, extraTags)
	}

	targetHours := computeTargetHours(cfg, waterTemp, waterOK)
	log.Printf("[planner] horizon=24h target_hours=%d water_temp=%.2f min=%d max=%d",
		targetHours, waterTemp, cfg.MinHours, cfg.MaxHours)

	sch, stats, err := solve(cfg, slots, prices, solar, targetHours)
	if err != nil {
		log.Printf("[planner] MILP failed: %v, falling back", err)
		sch = fallbackSchedule(cfg, slots)
		stats = fallbackStats(cfg, sch, prices, solar)
		tgt := len(cfg.FallbackNightHours) + len(cfg.FallbackAfternoonHours)
		return writePlan(cfg, slots, sch, prices, solar, stats, waterTemp, waterOK, tgt, "fallback", "infeasible", extraTags)
	}
	return writePlan(cfg, slots, sch, prices, solar, stats, waterTemp, waterOK, targetHours, "optimal", "", extraTags)
}
```

- [ ] **Step 2: Update `writePlan()` signature to accept extraTags**

In `planner.go`, change `writePlan` to:

```go
func writePlan(cfg *Config, slots []time.Time, sch []int, prices, solar []float64, stats planStats,
	waterTemp float64, waterOK bool, targetHours int, mode, missing string, extraTags map[string]string) error {
	applyTags := func(p *Point) *Point {
		for k, v := range extraTags {
			p.Tag(k, v)
		}
		return p
	}

	points := make([]*Point, 0, len(slots)+1)
	for t, slot := range slots {
		p := applyTags(NewPoint("pool_iqpump_plan").
			Tag("horizon", "24h").
			Tag("mode", mode)).
			Field("on", sch[t]).
			Field("cost_sek", stats.costPerSlot[t]).
			At(slot)
		priceField := 0.0
		if len(prices) > t && !math.IsNaN(prices[t]) {
			priceField = prices[t]
		}
		p.Field("price_sek_per_kwh", priceField)
		solarField := 0.0
		if len(solar) > t {
			solarField = solar[t]
		}
		p.Field("solar_kwh", solarField)
		points = append(points, p)
	}

	waterC := 0.0
	if waterOK {
		waterC = waterTemp
	}

	missingTag := missing
	if missingTag == "" {
		missingTag = "none"
	}

	summary := applyTags(NewPoint("pool_iqpump_plan_summary").
		Tag("horizon", "24h").
		Tag("mode", mode).
		Tag("missing_inputs", missingTag)).
		Field("planned_hours", stats.plannedHours).
		Field("target_hours", targetHours).
		Field("slot_minutes", cfg.SlotMinutes).
		Field("expected_cost_sek", stats.expectedCostSEK).
		Field("slack_hours", stats.slackHours).
		Field("water_temp_c", waterC).
		At(slots[0])
	points = append(points, summary)

	if err := cfg.WritePoints(points); err != nil {
		return err
	}
	log.Printf("[planner] plan written (mode=%s, slot=%dm): %.2f/%d hours, cost=%.2f SEK (slack=%.2f missing=%s)",
		mode, cfg.SlotMinutes, stats.plannedHours, targetHours, stats.expectedCostSEK, stats.slackHours, missingTag)
	return nil
}
```

- [ ] **Step 3: Update `runPlanner()` in `planner.go`**

Replace with:

```go
func runPlanner(cfg *Config) {
	if err := plan(cfg, time.Now().UTC(), map[string]string{"run": "live"}); err != nil {
		log.Printf("[planner] run failed: %v", err)
	}
}
```

- [ ] **Step 4: Run build + existing tests**

```bash
cd pool-pump-planner && go build ./... && go test ./...
```

Expected: all existing tests pass (PASS), no compile errors. If `fetchWaterTempAt` and `fetchSolarHistoricalKWh` don't exist yet, this step will fail. In that case, temporarily stub them in the affected file so the refactor compiles:

```go
// pool-pump-planner/vm.go, append at bottom (temporary until Task 2)
func (c *Config) fetchWaterTempAt(_ time.Time) (float64, bool) { return c.fetchWaterTemp() }

// pool-pump-planner/solar.go, append at bottom (temporary until Task 3)
func (c *Config) fetchSolarHistoricalKWh(slots []time.Time) []float64 { return make([]float64, len(slots)) }
```

Re-run `go build ./... && go test ./...`. Expected PASS.

- [ ] **Step 5: Commit**

```bash
git add pool-pump-planner/planner.go pool-pump-planner/vm.go pool-pump-planner/solar.go
git commit -m "refactor(pool-pump-planner): plan() takes now and extraTags" \
  -m "Preparation for the backfill subcommand. Live runs now emit a run=live tag on every plan point. No behavior change beyond the tag."
```

---

## Task 2: Replace the `fetchWaterTempAt` stub with a real point-in-time query

**Files:**
- Modify: `pool-pump-planner/vm.go`

- [ ] **Step 1: Replace the stub and add a helper method for point-in-time instant queries**

In `vm.go`, replace the temporary `fetchWaterTempAt` stub with:

```go
func (c *Config) queryPromInstantAt(promql string, at time.Time, lookbackDelta string) ([]promResult, error) {
	base := c.vmBaseURL()
	if base == "" {
		return nil, fmt.Errorf("VictoriaMetrics query URL not configured")
	}
	q := url.Values{}
	q.Set("query", promql)
	if !at.IsZero() {
		q.Set("time", strconv.FormatInt(at.Unix(), 10))
	}
	if lookbackDelta != "" {
		q.Set("lookback_delta", lookbackDelta)
	}
	return c.promGet(base+"/api/v1/query?"+q.Encode(), false)
}

func (c *Config) fetchWaterTempAt(at time.Time) (float64, bool) {
	result, err := c.queryPromInstantAt("pool_temperature_value", at, "")
	if err != nil {
		log.Printf("[planner] water temp query failed: %v", err)
		return 0, false
	}
	if len(result) == 0 || len(result[0].Values) == 0 {
		return 0, false
	}
	return result[0].Values[0].Value, true
}
```

Also change the existing `fetchWaterTemp` (lines 201–211) to a one-liner wrapper:

```go
func (c *Config) fetchWaterTemp() (float64, bool) {
	return c.fetchWaterTempAt(time.Time{})
}
```

`time.Time{}` (zero value) tells `queryPromInstantAt` to skip the `time` param, matching original behavior.

- [ ] **Step 2: Build + test**

```bash
cd pool-pump-planner && go build ./... && go test ./...
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add pool-pump-planner/vm.go
git commit -m "feat(pool-pump-planner): add fetchWaterTempAt for historical queries"
```

---

## Task 3: Replace the `fetchSolarHistoricalKWh` stub with a real VM query

**Files:**
- Modify: `pool-pump-planner/solar.go`

- [ ] **Step 1: Write a failing unit test for slot-to-hour kWh conversion**

Append to the `solar.go` file location's test — create `pool-pump-planner/solar_test.go` if it doesn't exist:

```go
package main

import (
	"math"
	"testing"
	"time"
)

func TestSamplesToKWhPerSlot(t *testing.T) {
	// 4 slots of 15 minutes, starting at a round epoch.
	start := time.Unix(1700000000, 0).UTC()
	slotMinutes := 15
	slots := []time.Time{
		start,
		start.Add(15 * time.Minute),
		start.Add(30 * time.Minute),
		start.Add(45 * time.Minute),
	}
	// Samples: slot 0 avg = 2 kW, slot 1 avg = 0.5 kW, slot 2 = no samples, slot 3 = 1 kW.
	samples := []promSample{
		{Timestamp: float64(start.Unix()), Value: 2.0},
		{Timestamp: float64(start.Add(15 * time.Minute).Unix()), Value: 0.5},
		// slot 2 skipped
		{Timestamp: float64(start.Add(45 * time.Minute).Unix()), Value: 1.0},
	}
	got := samplesToKWhPerSlot(samples, slots, slotMinutes)
	want := []float64{
		2.0 * 0.25, // 0.5 kWh
		0.5 * 0.25, // 0.125
		0,          // no data
		1.0 * 0.25, // 0.25
	}
	if len(got) != len(want) {
		t.Fatalf("len mismatch: got %d want %d", len(got), len(want))
	}
	for i := range want {
		if math.Abs(got[i]-want[i]) > 1e-9 {
			t.Errorf("slot %d: got %.4f want %.4f", i, got[i], want[i])
		}
	}
}
```

- [ ] **Step 2: Run test, verify failure**

```bash
cd pool-pump-planner && go test -run TestSamplesToKWhPerSlot ./...
```

Expected: FAIL (`samplesToKWhPerSlot undefined`).

- [ ] **Step 3: Replace the stub with real implementation**

In `solar.go`, replace the temporary `fetchSolarHistoricalKWh` stub with:

```go
// fetchSolarHistoricalKWh returns PV production (kWh) per slot, derived from the
// inverter's historical power metric. Used in backfill mode where forecast.solar
// has no historical endpoint.
func (c *Config) fetchSolarHistoricalKWh(slots []time.Time) []float64 {
	out := make([]float64, len(slots))
	if len(slots) == 0 {
		return out
	}
	slotSeconds := (slots[1].Unix() - slots[0].Unix())
	if slotSeconds <= 0 {
		slotSeconds = 900
	}
	slotRange := fmt.Sprintf("%ds", slotSeconds)
	promql := `avg_over_time(sigenergy_pv_power_power_kw{string="total"}[` + slotRange + `])`
	start := slots[0]
	end := slots[len(slots)-1]
	result, err := c.queryPromRange(promql, start, end, int(slotSeconds))
	if err != nil {
		log.Printf("[planner] historical solar query failed: %v", err)
		return out
	}
	if len(result) == 0 {
		return out
	}
	slotMinutes := int(slotSeconds / 60)
	return samplesToKWhPerSlot(result[0].Values, slots, slotMinutes)
}

// samplesToKWhPerSlot buckets kW samples onto slot start-times and converts to
// kWh assuming each sample represents the avg kW over one slot.
func samplesToKWhPerSlot(samples []promSample, slots []time.Time, slotMinutes int) []float64 {
	out := make([]float64, len(slots))
	slotHours := float64(slotMinutes) / 60.0
	byTs := make(map[int64]float64, len(samples))
	for _, s := range samples {
		ts := int64(s.Timestamp)
		byTs[ts] = s.Value
	}
	for i, slot := range slots {
		if v, ok := byTs[slot.Unix()]; ok {
			out[i] = v * slotHours
		}
	}
	return out
}
```

- [ ] **Step 4: Run test, verify pass**

```bash
cd pool-pump-planner && go test -run TestSamplesToKWhPerSlot ./...
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pool-pump-planner/solar.go pool-pump-planner/solar_test.go
git commit -m "feat(pool-pump-planner): add fetchSolarHistoricalKWh via PV metric"
```

---

## Task 4: Add `Backfill` flag to Config

**Files:**
- Modify: `pool-pump-planner/config.go`

- [ ] **Step 1: Add `Backfill bool` field to the `Config` struct**

In `config.go`, add after the `PlanTime string` field (line ~49):

```go
	// Backfill selects the historical data fetchers (historical solar via VM
	// instead of forecast.solar). Set only by the backfill subcommand.
	Backfill bool
```

- [ ] **Step 2: Build + tests**

```bash
cd pool-pump-planner && go build ./... && go test ./...
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add pool-pump-planner/config.go
git commit -m "feat(pool-pump-planner): add Backfill config flag"
```

---

## Task 5: Create `backfill.go` — types and date iterator

**Files:**
- Create: `pool-pump-planner/backfill.go`
- Create: `pool-pump-planner/backfill_test.go`

- [ ] **Step 1: Write failing test for date iteration**

Create `pool-pump-planner/backfill_test.go`:

```go
package main

import (
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
```

- [ ] **Step 2: Run test, verify failure**

```bash
cd pool-pump-planner && go test -run TestBackfillDates ./...
```

Expected: FAIL (`backfillDates undefined`).

- [ ] **Step 3: Create `backfill.go` with types and the date iterator**

Create `pool-pump-planner/backfill.go`:

```go
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

	// iterate newest first
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
```

- [ ] **Step 4: Run test, verify pass**

```bash
cd pool-pump-planner && go test -run TestBackfillDates ./...
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pool-pump-planner/backfill.go pool-pump-planner/backfill_test.go
git commit -m "feat(pool-pump-planner): add backfill types and date iterator"
```

---

## Task 6: Implement `runBackfill` and stdout table

**Files:**
- Modify: `pool-pump-planner/backfill.go`
- Modify: `pool-pump-planner/backfill_test.go`
- Modify: `pool-pump-planner/planner.go` (expose the per-slot schedule/stats through plan())

Current `plan()` writes directly to VM and returns only `error`. We need to also hand the per-day schedule/stats back to `runBackfill` for the table. Smallest change: let `plan()` return a `planReport` struct (fields empty on error) in addition to error; callers that don't care about the return (runPlanner) ignore it.

- [ ] **Step 1: Introduce `planReport` in `planner.go`**

At the top of `planner.go` (below `planStats`), add:

```go
// planReport is the outcome of one plan() invocation, used by the backfill
// table. Empty/zero fields on error.
type planReport struct {
	Mode        string
	Hours       float64
	TargetHours int
	CostSEK     float64
	SlackHours  float64
	Missing     string // "none" when nothing missing
	OnHours     []int  // unique local clock hours where any slot was on
}
```

Change `plan()` signature to return `(planReport, error)`. Update the three `writePlan(...)` call sites inside `plan()` to:

```go
// fallback path (missing inputs)
if err := writePlan(...); err != nil { return planReport{}, err }
return planReport{
	Mode:        "fallback",
	Hours:       stats.plannedHours,
	TargetHours: tgt,
	CostSEK:     stats.expectedCostSEK,
	SlackHours:  stats.slackHours,
	Missing:     missing,
	OnHours:     onHoursFromSchedule(sch, slots, cfg.Timezone),
}, nil
```

And analogous returns for the MILP-infeasible fallback and the optimal path (`Mode: "optimal"`, `Missing: "none"`). Ensure the `runPlanner` wrapper ignores the report:

```go
func runPlanner(cfg *Config) {
	if _, err := plan(cfg, time.Now().UTC(), map[string]string{"run": "live"}); err != nil {
		log.Printf("[planner] run failed: %v", err)
	}
}
```

- [ ] **Step 2: Write failing table-formatter test**

Append to `pool-pump-planner/backfill_test.go`:

```go
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
		t.Errorf("expected prices in missing col, got:\n%s", out)
	}
	// newest first: 04-20 should appear before 04-19
	pos20 := strings.Index(out, "2026-04-20")
	pos19 := strings.Index(out, "2026-04-19")
	if pos19 < pos20 || pos20 < 0 {
		t.Errorf("expected 04-20 before 04-19 (newest first); positions 04-20=%d 04-19=%d\n%s", pos20, pos19, out)
	}
	if !strings.Contains(out, "Failures: 0") {
		t.Errorf("expected Failures: 0, got:\n%s", out)
	}
}
```

Add `"strings"` to the test file's imports.

- [ ] **Step 3: Run test, verify pass**

```bash
cd pool-pump-planner && go test -run TestFormatBackfillTable ./...
```

Expected: PASS (`formatBackfillTable` was already implemented in Task 5).

- [ ] **Step 4: Implement `runBackfill` in `backfill.go`**

Append to `backfill.go`:

```go
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
			// Temporarily blank out credentials so WritePoints no-ops (it checks
			// both and logs-and-returns). Cheaper than threading a flag through.
			origHost, origToken = cfg.InfluxHost, cfg.InfluxToken
			cfg.InfluxHost, cfg.InfluxToken = "", ""
		}
		report, planErr := plan(cfg, anchorLocal.UTC(), tags)
		if dryRun {
			cfg.InfluxHost, cfg.InfluxToken = origHost, origToken
		}

		if planErr != nil {
			results = append(results, backfillResult{Date: d, Mode: "ERR", Err: planErr})
			continue
		}
		results = append(results, backfillResult{
			Date:        d,
			Mode:        report.Mode,
			Hours:       report.Hours,
			TargetHours: report.TargetHours,
			CostSEK:     report.CostSEK,
			SlackHours:  report.SlackHours,
			Missing:     report.Missing,
			OnHours:     report.OnHours,
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
```

- [ ] **Step 5: Build + run all tests**

```bash
cd pool-pump-planner && go build ./... && go test ./...
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add pool-pump-planner/planner.go pool-pump-planner/backfill.go pool-pump-planner/backfill_test.go
git commit -m "feat(pool-pump-planner): implement runBackfill and table formatter"
```

---

## Task 7: Wire the `backfill` subcommand in `main.go`

**Files:**
- Modify: `pool-pump-planner/main.go`

- [ ] **Step 1: Replace `main.go` with the subcommand dispatcher**

Replace the entire `main.go` body with:

```go
package main

import (
	"flag"
	"fmt"
	"log"
	"os"
	"strconv"
	"strings"
	"time"
)

func main() {
	log.SetFlags(log.LstdFlags | log.LUTC)

	if len(os.Args) >= 2 && os.Args[1] == "backfill" {
		cfg := loadConfig()
		if err := backfillCLI(cfg, os.Args[2:]); err != nil {
			log.Fatalf("backfill: %v", err)
		}
		return
	}

	once := flag.Bool("once", false, "run planner once and exit")
	flag.Parse()

	cfg := loadConfig()

	if *once || (len(os.Args) > 1 && os.Args[1] == "once") {
		runPlanner(cfg)
		return
	}

	// Run once on startup (mirroring python schedule.run_all), then daily at POOL_PLAN_TIME.
	runPlanner(cfg)

	hh, mm, err := parseHHMM(cfg.PlanTime)
	if err != nil {
		log.Fatalf("invalid POOL_PLAN_TIME %q: %v", cfg.PlanTime, err)
	}

	for {
		next := nextDailyRun(time.Now().In(cfg.Timezone), hh, mm)
		delay := time.Until(next)
		log.Printf("[planner] next run at %s (%.0fs)", next.Format(time.RFC3339), delay.Seconds())
		time.Sleep(delay)
		runPlanner(cfg)
	}
}

// backfillCLI parses subcommand flags and dispatches to runBackfill.
func backfillCLI(cfg *Config, args []string) error {
	fs := flag.NewFlagSet("backfill", flag.ExitOnError)
	days := fs.Int("days", 30, "number of calendar days to backfill")
	endFlag := fs.String("end", "", "last day to plan (YYYY-MM-DD, site-local) — default: yesterday")
	dryRun := fs.Bool("dry-run", false, "compute + print the table, skip VM writes")
	if err := fs.Parse(args); err != nil {
		return err
	}

	var end time.Time
	if *endFlag == "" {
		now := time.Now().In(cfg.Timezone)
		end = time.Date(now.Year(), now.Month(), now.Day(), 0, 0, 0, 0, cfg.Timezone).AddDate(0, 0, -1)
	} else {
		d, err := time.ParseInLocation("2006-01-02", *endFlag, cfg.Timezone)
		if err != nil {
			return fmt.Errorf("--end %q: %w", *endFlag, err)
		}
		end = d
	}

	return runBackfill(cfg, *days, end, *dryRun)
}

func parseHHMM(s string) (int, int, error) {
	parts := strings.Split(s, ":")
	if len(parts) != 2 {
		return 0, 0, fmt.Errorf("expected HH:MM, got %q", s)
	}
	hh, err := strconv.Atoi(strings.TrimSpace(parts[0]))
	if err != nil {
		return 0, 0, fmt.Errorf("invalid hour %q: %w", parts[0], err)
	}
	mm, err := strconv.Atoi(strings.TrimSpace(parts[1]))
	if err != nil {
		return 0, 0, fmt.Errorf("invalid minute %q: %w", parts[1], err)
	}
	if hh < 0 || hh > 23 || mm < 0 || mm > 59 {
		return 0, 0, fmt.Errorf("out of range HH:MM in %q", s)
	}
	return hh, mm, nil
}

func nextDailyRun(now time.Time, hh, mm int) time.Time {
	candidate := time.Date(now.Year(), now.Month(), now.Day(), hh, mm, 0, 0, now.Location())
	if !candidate.After(now) {
		candidate = candidate.AddDate(0, 0, 1)
	}
	return candidate
}
```

- [ ] **Step 2: Build + test**

```bash
cd pool-pump-planner && go build ./... && go test ./...
```

Expected: PASS.

- [ ] **Step 3: Smoke-test the CLI shape with `--help`-style invocation**

```bash
cd pool-pump-planner && go run . backfill --help 2>&1 || true
```

Expected: usage output lists `-days`, `-end`, `-dry-run`. (`--help` may exit with status 2 for unknown flag — OK, we're just confirming flags are registered.)

- [ ] **Step 4: Commit**

```bash
git add pool-pump-planner/main.go
git commit -m "feat(pool-pump-planner): add backfill subcommand dispatcher"
```

---

## Task 8: Update the Grafana pool-plan panel to filter `run="live"`

**Files:**
- Modify: `grafana/src/panels/pool.ts`
- Modify: `grafana/dist/...` (regenerated JSON — whatever `npm run build` emits)

- [ ] **Step 1: Add `{run="live"}` to the three plan-panel queries**

In `grafana/src/panels/pool.ts`, change lines 105–107 from:

```ts
.withTarget(vmExpr('A', 'last_over_time(pool_iqpump_plan_on[$__interval])', 'on'))
.withTarget(vmExpr('B', 'last_over_time(pool_iqpump_plan_price_sek_per_kwh[$__interval])', 'price_sek_per_kwh'))
.withTarget(vmExpr('C', 'last_over_time(pool_iqpump_plan_solar_kwh[$__interval])', 'solar_kwh'))
```

To:

```ts
.withTarget(vmExpr('A', 'last_over_time(pool_iqpump_plan_on{run="live"}[$__interval])', 'on'))
.withTarget(vmExpr('B', 'last_over_time(pool_iqpump_plan_price_sek_per_kwh{run="live"}[$__interval])', 'price_sek_per_kwh'))
.withTarget(vmExpr('C', 'last_over_time(pool_iqpump_plan_solar_kwh{run="live"}[$__interval])', 'solar_kwh'))
```

- [ ] **Step 2: Regenerate the dashboard JSON via `npm run build`**

```bash
cd grafana && npm install --no-audit --no-fund 2>&1 | tail -5 && GRAFANA_SKIP_UPLOAD=1 npm run build
```

Expected: exits 0, produces updated JSON under whatever path the build script writes to (check `git status` after).

- [ ] **Step 3: Verify git-diff only shows the three-query change**

```bash
git status --short grafana/
git diff grafana/
```

Expected: changes in `grafana/src/panels/pool.ts` and the generated JSON file. If the diff is larger (unrelated panels churned), open the diff and confirm the extra changes are harmless re-ordering.

- [ ] **Step 4: Commit**

```bash
git add grafana/src/panels/pool.ts grafana/
git commit -m "feat(grafana): filter pool-plan panel to run=live"
```

---

## Task 9: End-to-end verification against live VM

**Files:** none (manual checks).

- [ ] **Step 1: Dry-run 3 days against live VM**

Ensure `INFLUX_HOST`, `INFLUX_TOKEN` etc. are set (they are via the shell or `.env.local`):

```bash
cd pool-pump-planner
set -a && source ../fetcher-core/python/.env.local && set +a
go run . backfill --days=3 --dry-run 2>&1 | tee /tmp/iot_fetcher/backfill-dry.log
```

Expected: 3-row stdout table printed, latest 3 days (ending yesterday). Each row shows a mode + hours + cost. `Failures: 0`. Log lines from the planner appear above the table. No VM writes (confirmed because `INFLUX_HOST` was temporarily cleared inside the loop).

If any row shows `ERR`, inspect the log and fix before proceeding.

- [ ] **Step 2: Real run of 3 days**

```bash
cd pool-pump-planner
go run . backfill --days=3 2>&1 | tee /tmp/iot_fetcher/backfill-real.log
```

Expected: same stdout table. This time `[influx] wrote N points` lines appear.

- [ ] **Step 3: Verify VM received the tagged points**

```bash
scripts/vm-query.sh query 'count_over_time(pool_iqpump_plan_on{run="backfill"}[30d])'
```

Expected: `status=success`, a non-zero count (≈ 3 days × 96 slots = ~288).

- [ ] **Step 4: Run the full 30-day backfill**

```bash
cd pool-pump-planner && go run . backfill 2>&1 | tee /tmp/iot_fetcher/backfill-30d.log
```

Expected: 30-row table. Glance at hours/cost for sanity. Document any oddities in the PR description.

- [ ] **Step 5: (No commit for this task — verification only.)**

---

## Task 10: Open the PR

- [ ] **Step 1: Push the branch**

```bash
cd /Users/filip/Desktop/own/iot_fetcher/.claude/worktrees/agent-a721076e
git push -u origin HEAD
```

Expected: "Branch 'fix/pool-pump-planner-temperature-metric-typo' set up to track 'origin/...'" (note: branch name from the worktree; we're reusing it since the prior temp-typo commit is already in main under a different SHA). If the push is rejected for non-fast-forward because the remote branch still exists, push with `--force-with-lease` since no collaborators share this branch.

- [ ] **Step 2: Create the PR**

```bash
gh pr create --title "feat(pool-pump-planner): backfill subcommand + run tag" --body "$(cat <<'EOF'
## Summary

Adds `pool-pump-planner backfill` — a one-shot CLI that replays the planner for each of the last N days (default 30), anchored at each day's `POOL_PLAN_TIME`, writes VM points tagged `run=backfill`, and prints a stdout summary table.

Live runs also now carry `run=live` on every plan point. The existing Grafana plan panel is filtered to `{run="live"}` so backfill writes don't pollute it.

Historical solar comes from the real PV metric (`sigenergy_pv_power_power_kw{string="total"}`) aggregated per slot — forecast.solar has no real historical endpoint.

## Design

See `docs/superpowers/specs/2026-04-21-pool-planner-backfill-design.md` (committed in this branch).

## Test plan

Local (pre-merge):
- [x] ``go test ./pool-pump-planner/...`` passes
- [x] ``go run . backfill --dry-run --days=3`` prints a 3-row table, 0 VM writes
- [x] ``go run . backfill --days=3`` writes tagged points; ``count_over_time(pool_iqpump_plan_on{run="backfill"}[7d])`` > 0
- [x] ``go run . backfill`` 30-day run completes

Post-merge:
- [ ] Grafana dashboard deploys; the pool-plan panel shows only live data
- [ ] Ad-hoc query on ``{run="backfill", anchor_date=...}`` returns each backfilled day

## Follow-up (not in this PR)

PV shadow mask for the live planner — the inverter curve shows morning shade until ~10h and a peak at 14h (vs solar noon ~13h). Mask env var noted in the spec.
EOF
)"
```

Return the PR URL printed by `gh pr create`.

---

## Self-Review

Spec coverage:

- **CLI shape (days/end/dry-run):** Task 7 ✓
- **Run tag on live + backfill:** Tasks 1 (live side) + 6 (backfill side via tags map) ✓
- **`cfg.Backfill` bool:** Task 4 ✓
- **`fetchWaterTempAt`:** Task 2 ✓
- **`fetchSolarHistoricalKWh` via sigenergy metric:** Task 3 ✓
- **`backfill.go` (types, orchestration, printer):** Tasks 5 + 6 ✓
- **Stdout table, newest first, fallback/ERR rows:** Task 6 (implementation) + test in Task 6 ✓
- **Per-day non-fatal errors, exit 1 if all fail:** Task 6 (`anyOK` guard) ✓
- **`--dry-run` skips writes:** Task 6 (credential blanking) ✓
- **Idempotency via (measurement, tags, ts):** implicit in line protocol — no test needed, spec says so ✓
- **Grafana panel filtered to `run="live"`:** Task 8 ✓
- **Dashboard regenerated via the TS build:** Task 8 ✓
- **Non-goal: no forecast.solar history:** respected ✓
- **Non-goal: no retro-writing `run=live` onto old points:** respected ✓
- **Follow-up: PV shadow mask noted in PR body + spec:** Task 10 ✓

No TBDs or "handle appropriately" placeholders. All code shown in full. Method and type names are consistent across tasks (`plan`, `planReport`, `runBackfill`, `backfillResult`, `backfillDates`, `formatBackfillTable`, `fetchSolarHistoricalKWh`, `fetchWaterTempAt`).

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-21-pool-planner-backfill.md`.
