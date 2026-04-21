package main

import (
	"fmt"
	"log"
	"math"
	"strings"
	"time"
)

type planStats struct {
	plannedHours    float64
	expectedCostSEK float64
	slackHours      float64
	costPerSlot     []float64
}

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

// planInputs are the fetched inputs shared between the MILP solve and any
// alternative schedules computed on the same day (baselines). Populated even
// when plan() returns an error after the fetch stage, so callers can still
// use the inputs for comparison runs.
type planInputs struct {
	Slots     []time.Time
	Prices    []float64
	Solar     []float64
	WaterTemp float64
	WaterOK   bool
}

func nan() float64 { return math.NaN() }

// runPlanner is the top-level entry. Any error is logged so the caller can
// keep running on a schedule without crashing.
func runPlanner(cfg *Config) {
	if _, _, err := plan(cfg, time.Now().UTC(), map[string]string{"run": "live"}); err != nil {
		log.Printf("[planner] run failed: %v", err)
	}
}

func plan(cfg *Config, now time.Time, extraTags map[string]string) (planReport, planInputs, error) {
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

	inputs := planInputs{
		Slots:     slots,
		Prices:    prices,
		Solar:     solar,
		WaterTemp: waterTemp,
		WaterOK:   waterOK,
	}

	if cfg.DryRun {
		printInputs(cfg, slots, prices, solar, waterTemp, waterOK)
	}

	minSlots := cfg.MinHours * cfg.SlotsPerHour()
	priceCount := countNonNaN(prices)
	if priceCount < horizonSlots {
		log.Printf("[planner] partial prices: %d/%d slots covered (NaN slots are blocked in MILP)", priceCount, horizonSlots)
	}
	missing := missingInputs(priceCount, waterOK, minSlots)
	if missing != "" {
		log.Printf("[planner] missing inputs %s, falling back to static schedule", missing)
		sch := fallbackSchedule(cfg, slots)
		stats := fallbackStats(cfg, sch, prices, solar)
		tgt := len(cfg.FallbackNightHours) + len(cfg.FallbackAfternoonHours)
		if err := writePlan(cfg, slots, sch, prices, solar, stats, waterTemp, waterOK, tgt, "fallback", missing, extraTags); err != nil {
			return planReport{}, inputs, err
		}
		return planReport{
			Mode:        "fallback",
			Hours:       stats.plannedHours,
			TargetHours: tgt,
			CostSEK:     stats.expectedCostSEK,
			SlackHours:  stats.slackHours,
			Missing:     missing,
			OnHours:     onHoursFromSchedule(sch, slots, cfg.Timezone),
		}, inputs, nil
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
		if err := writePlan(cfg, slots, sch, prices, solar, stats, waterTemp, waterOK, tgt, "fallback", "infeasible", extraTags); err != nil {
			return planReport{}, inputs, err
		}
		return planReport{
			Mode:        "fallback",
			Hours:       stats.plannedHours,
			TargetHours: tgt,
			CostSEK:     stats.expectedCostSEK,
			SlackHours:  stats.slackHours,
			Missing:     "infeasible",
			OnHours:     onHoursFromSchedule(sch, slots, cfg.Timezone),
		}, inputs, nil
	}
	if err := writePlan(cfg, slots, sch, prices, solar, stats, waterTemp, waterOK, targetHours, "optimal", "", extraTags); err != nil {
		return planReport{}, inputs, err
	}
	return planReport{
		Mode:        "optimal",
		Hours:       stats.plannedHours,
		TargetHours: targetHours,
		CostSEK:     stats.expectedCostSEK,
		SlackHours:  stats.slackHours,
		Missing:     "none",
		OnHours:     onHoursFromSchedule(sch, slots, cfg.Timezone),
	}, inputs, nil
}

// missingInputs returns a comma-separated list of inputs missing by enough
// that we cannot produce an optimal plan. Prices only count as missing if
// fewer than minSlots are priced — partial coverage is acceptable because the
// MILP blocks NaN-priced slots (see solve()).
func missingInputs(priceCount int, waterOK bool, minSlots int) string {
	missing := []string{}
	if priceCount < minSlots {
		missing = append(missing, "prices")
	}
	if !waterOK {
		missing = append(missing, "water_temp")
	}
	return strings.Join(missing, ",")
}

func countNonNaN(xs []float64) int {
	n := 0
	for _, x := range xs {
		if !math.IsNaN(x) {
			n++
		}
	}
	return n
}

func computeTargetHours(cfg *Config, waterTemp float64, waterOK bool) int {
	if !waterOK || cfg.HeatingRateCPerHour <= 0 {
		return cfg.TargetHours
	}
	delta := cfg.TargetTempC - waterTemp
	if delta < 0 {
		delta = 0
	}
	needed := int(math.Ceil(delta / cfg.HeatingRateCPerHour))
	if needed < cfg.TargetHours {
		needed = cfg.TargetHours
	}
	if needed > cfg.MaxHours {
		needed = cfg.MaxHours
	}
	return needed
}

func fallbackSchedule(cfg *Config, slots []time.Time) []int {
	on := map[int]bool{}
	for _, h := range cfg.FallbackNightHours {
		on[h] = true
	}
	for _, h := range cfg.FallbackAfternoonHours {
		on[h] = true
	}
	out := make([]int, len(slots))
	for i, s := range slots {
		if on[s.In(cfg.Timezone).Hour()] {
			out[i] = 1
		}
	}
	return out
}

// slotCost is the marginal SEK cost of running the pump for one slot.
// Spot price is charged on full consumption (opportunity cost of self-consumed
// solar — the pump could otherwise be off and the energy used/sold elsewhere).
// Transfer fee and energy tax are charged only on grid-imported kWh. VAT is
// applied to the entire bill (Sweden charges moms on the energiskatt too).
//
// TODO: sell-back revenue. The real opportunity cost of self-consumed solar
// is the nätnytta export credit (~12.48 öre/kWh on the user's invoice), not
// full spot. Charging spot here overstates the cost of solar runs.
func slotCost(cfg *Config, slotEnergy, p, solarKWh float64) float64 {
	grid := slotEnergy - solarKWh
	if grid < 0 {
		grid = 0
	}
	pre := slotEnergy*p + grid*(cfg.TransferFeeSEKPerKWh+cfg.EnergyTaxSEKPerKWh)
	return pre * (1 + cfg.VATFraction)
}

func fallbackStats(cfg *Config, sch []int, prices []float64, solar []float64) planStats {
	slotEnergy := cfg.PumpKW * cfg.SlotHours()
	cps := make([]float64, len(sch))
	total := 0.0
	for t := range sch {
		p := 0.0
		if len(prices) > t && !math.IsNaN(prices[t]) {
			p = prices[t]
		}
		s := 0.0
		if len(solar) > t {
			s = solar[t]
		}
		c := slotCost(cfg, slotEnergy, p, s)
		cps[t] = c
		if sch[t] == 1 {
			total += c
		}
	}
	totalSlots := 0
	for _, v := range sch {
		totalSlots += v
	}
	return planStats{
		plannedHours:    float64(totalSlots) * cfg.SlotHours(),
		expectedCostSEK: total,
		slackHours:      0,
		costPerSlot:     cps,
	}
}

func solve(cfg *Config, slots []time.Time, prices, solar []float64, targetHours int) ([]int, planStats, error) {
	T := cfg.HorizonSlots()
	slotEnergy := cfg.PumpKW * cfg.SlotHours()

	costs := make([]float64, T)
	blocked := map[int]bool{}
	blockedHourSet := map[int]bool{}
	for _, h := range cfg.BlockedHours {
		blockedHourSet[h] = true
	}

	for t := 0; t < T; t++ {
		p := prices[t]
		if math.IsNaN(p) {
			costs[t] = 0
			blocked[t] = true
			continue
		}
		costs[t] = slotCost(cfg, slotEnergy, p, solar[t])

		if blockedHourSet[slots[t].In(cfg.Timezone).Hour()] {
			blocked[t] = true
		}
	}

	slotsPerHour := cfg.SlotsPerHour()
	minSlots := cfg.MinHours * slotsPerHour
	targetSlots := targetHours * slotsPerHour
	maxSlots := cfg.MaxHours * slotsPerHour

	available := 0
	for t := 0; t < T; t++ {
		if !blocked[t] {
			available++
		}
	}
	if available < minSlots {
		return nil, planStats{}, fmt.Errorf("only %d available slots after blocking (need >= %d)", available, minSlots)
	}
	if maxSlots > available {
		maxSlots = available
	}

	result, err := solveMILP(lpInput{
		costs:       costs,
		blocked:     blocked,
		minSlots:    minSlots,
		targetSlots: targetSlots,
		maxSlots:    maxSlots,
		maxStarts:   cfg.MaxStarts,
	})
	if err != nil {
		return nil, planStats{}, err
	}

	total := 0.0
	runSlots := 0
	for t, on := range result.schedule {
		if on == 1 {
			total += costs[t]
			runSlots++
		}
	}
	stats := planStats{
		plannedHours:    float64(runSlots) * cfg.SlotHours(),
		expectedCostSEK: total,
		slackHours:      result.slack * cfg.SlotHours(),
		costPerSlot:     costs,
	}
	return result.schedule, stats, nil
}

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

	// missing_inputs is a tag (not a field) because VictoriaMetrics drops
	// string fields from InfluxDB line protocol. Values are low-cardinality:
	// "none", "prices", "water_temp", "prices,water_temp", "infeasible".
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

	if cfg.DryRun {
		printSchedule(cfg, slots, sch, prices, solar, stats.costPerSlot)
		log.Printf("[planner] DRY RUN (mode=%s, slot=%dm): %.2f/%d hours, cost=%.2f SEK (slack=%.2f missing=%s) — skipping write",
			mode, cfg.SlotMinutes, stats.plannedHours, targetHours, stats.expectedCostSEK, stats.slackHours, missingTag)
		return nil
	}

	// Clean snapshot for live runs: delete previous {run="live"} samples within
	// this plan's horizon (slots[0] forward). Forward-only scoping preserves
	// live-plan records from earlier days. Backfill/baseline paths are untouched
	// because they use run="backfill"/"baseline_*". Log-and-continue on error —
	// a delete failure is no worse than the pre-fix duplicate state.
	if extraTags["run"] == "live" && len(slots) > 0 {
		sel := `{__name__=~"pool_iqpump_plan.*",run="live"}`
		if err := cfg.deleteSeries(sel, slots[0]); err != nil {
			log.Printf("[planner] delete_series failed (continuing): %v", err)
		}
	}

	if err := cfg.WritePoints(points); err != nil {
		return err
	}
	log.Printf("[planner] plan written (mode=%s, slot=%dm): %.2f/%d hours, cost=%.2f SEK (slack=%.2f missing=%s)",
		mode, cfg.SlotMinutes, stats.plannedHours, targetHours, stats.expectedCostSEK, stats.slackHours, missingTag)
	return nil
}

func printInputs(cfg *Config, slots []time.Time, prices, solar []float64, waterTemp float64, waterOK bool) {
	fmt.Printf("\n=== INPUTS ===\n")
	fmt.Printf("now=%s  timezone=%s  horizon=%d slots (%d min each)\n",
		slots[0].In(cfg.Timezone).Format(time.RFC3339), cfg.Timezone, len(slots), cfg.SlotMinutes)
	if waterOK {
		fmt.Printf("water_temp=%.2f°C\n", waterTemp)
	} else {
		fmt.Printf("water_temp=<missing>\n")
	}
	priceCount, solarCount := 0, 0
	for _, p := range prices {
		if !math.IsNaN(p) {
			priceCount++
		}
	}
	for _, s := range solar {
		if !math.IsNaN(s) && s != 0 {
			solarCount++
		}
	}
	fmt.Printf("prices: %d/%d slots covered\n", priceCount, len(prices))
	fmt.Printf("solar:  %d/%d slots with forecast > 0\n\n", solarCount, len(solar))
	fmt.Printf("  %-20s  %10s  %10s\n", "slot (local)", "price", "solar_kWh")
	for t, slot := range slots {
		price := "  nan"
		if !math.IsNaN(prices[t]) {
			price = fmt.Sprintf("%8.4f", prices[t])
		}
		fmt.Printf("  %-20s  %10s  %10.3f\n", slot.In(cfg.Timezone).Format("2006-01-02 15:04"), price, solar[t])
	}
	fmt.Println()
}

func printSchedule(cfg *Config, slots []time.Time, sch []int, prices, solar, costPerSlot []float64) {
	fmt.Printf("\n=== SCHEDULE ===\n")
	fmt.Printf("  %-20s  %3s  %10s  %10s  %10s\n", "slot (local)", "on", "price", "solar_kWh", "cost_sek")
	for t, slot := range slots {
		price := "  nan"
		if !math.IsNaN(prices[t]) {
			price = fmt.Sprintf("%8.4f", prices[t])
		}
		fmt.Printf("  %-20s  %3d  %10s  %10.3f  %10.4f\n",
			slot.In(cfg.Timezone).Format("2006-01-02 15:04"), sch[t], price, solar[t], costPerSlot[t])
	}
	fmt.Println()
}
