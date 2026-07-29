package main

import (
	"math"
	"testing"
	"time"
)

func TestSlotCost(t *testing.T) {
	cfg := &Config{
		TransferFeeSEKPerKWh: 0.2584,
		EnergyTaxSEKPerKWh:   0.36,
		VATFraction:          0.25,
	}

	tests := []struct {
		name       string
		slotEnergy float64
		price      float64
		solar      float64
		want       float64
	}{
		{
			// 1 kWh slot @ 0.5 SEK spot, no solar → full grid import.
			// (0.5 + 0.2584 + 0.36) × 1.25 = 1.398.
			name:       "no solar, full grid import",
			slotEnergy: 1.0,
			price:      0.5,
			solar:      0.0,
			want:       1.398,
		},
		{
			// Solar covers the whole slot → no transfer/tax, only opportunity-
			// cost of spot on consumption. 0.5 × 1.25 = 0.625.
			name:       "solar covers full slot",
			slotEnergy: 1.0,
			price:      0.5,
			solar:      1.0,
			want:       0.625,
		},
		{
			// Partial solar (0.6 kWh of 1.0): grid = 0.4.
			// pre = 1.0×0.5 + 0.4×(0.2584+0.36) = 0.5 + 0.24736 = 0.74736.
			// cost = 0.74736 × 1.25 = 0.9342.
			name:       "partial solar",
			slotEnergy: 1.0,
			price:      0.5,
			solar:      0.6,
			want:       0.9342,
		},
		{
			// Solar exceeds slot energy (exporting). Grid clamps to 0.
			// 1.0×0.5 × 1.25 = 0.625, same as "solar covers full slot".
			name:       "solar exceeds consumption",
			slotEnergy: 1.0,
			price:      0.5,
			solar:      1.5,
			want:       0.625,
		},
		{
			// Real-slot hand check from the plan doc: 12:00 today, 15-min
			// slot, pumpKW=4 → slotEnergy=1.0, p=0.1807, solar=0.629.
			// grid = 0.371
			// pre = 1.0×0.1807 + 0.371×(0.2584+0.36) = 0.1807 + 0.2294264 = 0.4101264.
			// cost = 0.4101264 × 1.25 = 0.5126580.
			name:       "plan doc hand-check at 12:00",
			slotEnergy: 1.0,
			price:      0.1807,
			solar:      0.629,
			want:       0.512658,
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			got := slotCost(cfg, tc.slotEnergy, tc.price, tc.solar)
			if math.Abs(got-tc.want) > 1e-4 {
				t.Errorf("slotCost(%.4f, %.4f, %.4f) = %.6f, want %.6f",
					tc.slotEnergy, tc.price, tc.solar, got, tc.want)
			}
		})
	}
}

func TestBuildPlanPointsOmitsNaNPrice(t *testing.T) {
	cfg := &Config{SlotMinutes: 15}
	slots := []time.Time{
		time.Date(2026, 4, 23, 0, 0, 0, 0, time.UTC),
		time.Date(2026, 4, 23, 0, 15, 0, 0, time.UTC),
		time.Date(2026, 4, 23, 0, 30, 0, 0, time.UTC),
	}
	sch := []int{0, 1, 0}
	prices := []float64{0.42, math.NaN(), -0.001}
	solar := []float64{0, 0, 0}
	stats := planStats{costPerSlot: []float64{0, 0, 0}}
	tags := map[string]string{"run": "live", "plan_date": "2026-04-23"}

	points := buildPlanPoints(cfg, slots, sch, prices, solar, solar, stats, 0, false, 6, "optimal", "", tags)

	if len(points) != len(slots)+1 {
		t.Fatalf("expected %d points (slots + summary), got %d", len(slots)+1, len(points))
	}

	checkPrice := func(i int, wantField bool, wantVal float64) {
		p := points[i]
		got, ok := p.Fields["price_sek_per_kwh"]
		if wantField {
			if !ok {
				t.Errorf("slot %d: expected price_sek_per_kwh field, missing", i)
				return
			}
			if v, _ := got.(float64); v != wantVal {
				t.Errorf("slot %d: price_sek_per_kwh = %v, want %v", i, got, wantVal)
			}
		} else if ok {
			t.Errorf("slot %d: expected no price_sek_per_kwh field (NaN input), got %v", i, got)
		}
	}
	checkPrice(0, true, 0.42)
	checkPrice(1, false, 0)
	checkPrice(2, true, -0.001) // negative prices are legitimate SE4 values

	// plan_date + run tags must propagate to every slot point and the summary.
	for i, p := range points {
		if p.Tags["plan_date"] != "2026-04-23" {
			t.Errorf("point %d: plan_date tag = %q, want %q", i, p.Tags["plan_date"], "2026-04-23")
		}
		if p.Tags["run"] != "live" {
			t.Errorf("point %d: run tag = %q, want %q", i, p.Tags["run"], "live")
		}
	}
}
