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

func nan() float64 { return math.NaN() }

// runPlanner is the top-level entry. Any error is logged so the caller can
// keep running on a schedule without crashing.
func runPlanner(cfg *Config) {
	if err := plan(cfg); err != nil {
		log.Printf("[planner] run failed: %v", err)
	}
}

func plan(cfg *Config) error {
	slotMinutes := cfg.SlotMinutes
	horizonSlots := cfg.HorizonSlots()

	now := time.Now().UTC().Truncate(time.Hour)
	slots := make([]time.Time, horizonSlots)
	for i := 0; i < horizonSlots; i++ {
		slots[i] = now.Add(time.Duration(i*slotMinutes) * time.Minute)
	}

	prices := cfg.fetchHourlyPrices(slots)
	solar := cfg.fetchSolarForecast(slots)
	waterTemp, waterOK := cfg.fetchWaterTemp()

	missing := missingInputs(prices, waterOK, horizonSlots)
	if missing != "" {
		log.Printf("[planner] missing inputs %s, falling back to static schedule", missing)
		sch := fallbackSchedule(cfg, slots)
		stats := fallbackStats(cfg, sch, prices, solar)
		tgt := len(cfg.FallbackNightHours) + len(cfg.FallbackAfternoonHours)
		return writePlan(cfg, slots, sch, prices, solar, stats, waterTemp, waterOK, tgt, "fallback", missing)
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
		return writePlan(cfg, slots, sch, prices, solar, stats, waterTemp, waterOK, tgt, "fallback", "infeasible")
	}
	return writePlan(cfg, slots, sch, prices, solar, stats, waterTemp, waterOK, targetHours, "optimal", "")
}

func missingInputs(prices []float64, waterOK bool, want int) string {
	missing := []string{}
	if len(prices) < want {
		missing = append(missing, "prices")
	} else {
		have := 0
		for _, p := range prices {
			if !math.IsNaN(p) {
				have++
			}
		}
		if have < want {
			missing = append(missing, "prices")
		}
	}
	if !waterOK {
		missing = append(missing, "water_temp")
	}
	return strings.Join(missing, ",")
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
		gridKWh := slotEnergy - s
		if gridKWh < 0 {
			gridKWh = 0
		}
		c := slotEnergy*p + gridKWh*cfg.GridFeeSEKPerKWh
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
		grid := slotEnergy - solar[t]
		if grid < 0 {
			grid = 0
		}
		costs[t] = slotEnergy*p + grid*cfg.GridFeeSEKPerKWh

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
	waterTemp float64, waterOK bool, targetHours int, mode, missing string) error {
	points := make([]*Point, 0, len(slots)+1)
	for t, slot := range slots {
		p := NewPoint("pool_iqpump_plan").
			Tag("horizon", "24h").
			Tag("mode", mode).
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

	summary := NewPoint("pool_iqpump_plan_summary").
		Tag("horizon", "24h").
		Tag("mode", mode).
		Field("planned_hours", stats.plannedHours).
		Field("target_hours", targetHours).
		Field("slot_minutes", cfg.SlotMinutes).
		Field("expected_cost_sek", stats.expectedCostSEK).
		Field("slack_hours", stats.slackHours).
		Field("water_temp_c", waterC).
		Field("missing_inputs", missing).
		At(slots[0])
	points = append(points, summary)

	if err := cfg.WritePoints(points); err != nil {
		return err
	}
	missingTag := missing
	if missingTag == "" {
		missingTag = "-"
	}
	log.Printf("[planner] plan written (mode=%s, slot=%dm): %.2f/%d hours, cost=%.2f SEK (slack=%.2f missing=%s)",
		mode, cfg.SlotMinutes, stats.plannedHours, targetHours, stats.expectedCostSEK, stats.slackHours, missingTag)
	return nil
}
