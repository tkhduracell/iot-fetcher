package main

import (
	"math"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestWriteLPRoundtrip(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "problem.lp")
	in := lpInput{
		costs:       []float64{1, 2, 3, 4},
		blocked:     map[int]bool{1: true},
		minSlots:    1,
		targetSlots: 2,
		maxSlots:    3,
		maxStarts:   2,
	}
	if err := writeLP(path, in); err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	s := string(data)
	for _, want := range []string{
		"Minimize",
		"obj:",
		"slack",
		"c_min:",
		"c_target:",
		"c_max:",
		"c_maxstarts:",
		"c_blk_1:",
		"Binary",
		"End",
	} {
		if !strings.Contains(s, want) {
			t.Errorf("LP output missing %q\n%s", want, s)
		}
	}
}

// TestWriteLPSkipsNonFiniteCoefficients reproduces the input shape from a day
// with missing prices and a zeroed water temp: every cost is either 0 or
// NaN/+Inf/-Inf. The generated LP must never contain a "NaN"/"Inf" token —
// CBC's LP reader either drops such terms silently, misparses them as a
// variable name, or (depending on build) fails objective parsing outright
// with "CoinLpIO::read_monom_obj" / "Unable to read objective function".
func TestWriteLPSkipsNonFiniteCoefficients(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "problem.lp")
	in := lpInput{
		costs:       []float64{0, math.NaN(), math.Inf(1), math.Inf(-1), 0},
		blocked:     map[int]bool{},
		minSlots:    1,
		targetSlots: 2,
		maxSlots:    3,
		maxStarts:   2,
	}
	if err := writeLP(path, in); err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	s := string(data)
	for _, bad := range []string{"NaN", "+Inf", "-Inf", "Inf "} {
		if strings.Contains(s, bad) {
			t.Errorf("LP output must not contain %q\n%s", bad, s)
		}
	}
	// The objective must still be non-empty: the bigM*slack term always
	// gets written, so CBC always has something to minimize.
	objLine := s[strings.Index(s, "obj:"):strings.Index(s, "Subject To")]
	if !strings.Contains(objLine, "slack") {
		t.Errorf("objective must retain the slack term when all costs are non-finite/zero:\n%s", objLine)
	}
}

// TestSolveMILPRejectsDegenerateInput exercises the validation gate added
// ahead of LP generation: an empty horizon and all-non-finite costs must
// fail fast with a clear error instead of ever reaching CBC.
func TestSolveMILPRejectsDegenerateInput(t *testing.T) {
	cases := []struct {
		name string
		in   lpInput
	}{
		{
			name: "no slots",
			in:   lpInput{costs: nil, minSlots: 0, targetSlots: 0, maxSlots: 0, maxStarts: 1},
		},
		{
			name: "all non-finite costs",
			in: lpInput{
				costs:       []float64{math.NaN(), math.Inf(1), math.Inf(-1)},
				minSlots:    0,
				targetSlots: 0,
				maxSlots:    3,
				maxStarts:   1,
			},
		},
		{
			name: "min exceeds max",
			in: lpInput{
				costs:       []float64{1, 2, 3},
				minSlots:    3,
				targetSlots: 3,
				maxSlots:    1,
				maxStarts:   1,
			},
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if _, err := solveMILP(tc.in); err == nil {
				t.Errorf("expected solveMILP to reject degenerate input, got nil error")
			}
		})
	}
}

func TestParseCBCSolution(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "sol.txt")
	content := "Optimal - objective value 10.5\n" +
		"0 x_0 1 0\n" +
		"1 x_1 0 0\n" +
		"2 x_2 1 0\n" +
		"3 slack 0.5 0\n"
	if err := os.WriteFile(path, []byte(content), 0644); err != nil {
		t.Fatal(err)
	}
	res, err := parseCBCSolution(path, 3)
	if err != nil {
		t.Fatal(err)
	}
	if res.schedule[0] != 1 || res.schedule[1] != 0 || res.schedule[2] != 1 {
		t.Errorf("bad schedule: %v", res.schedule)
	}
	if res.slack != 0.5 {
		t.Errorf("bad slack: %v", res.slack)
	}
}
