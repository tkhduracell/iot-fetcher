package main

import (
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
