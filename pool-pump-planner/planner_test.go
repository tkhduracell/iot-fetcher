package main

import (
	"math"
	"testing"
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
