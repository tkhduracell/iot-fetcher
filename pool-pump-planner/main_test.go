package main

import "testing"

func TestParseHHMM(t *testing.T) {
	good := []struct {
		in     string
		hh, mm int
	}{
		{"14:05", 14, 5},
		{"00:00", 0, 0},
		{"23:59", 23, 59},
		{" 9:30", 9, 30},
	}
	for _, tc := range good {
		hh, mm, err := parseHHMM(tc.in)
		if err != nil {
			t.Errorf("parseHHMM(%q) unexpected error: %v", tc.in, err)
			continue
		}
		if hh != tc.hh || mm != tc.mm {
			t.Errorf("parseHHMM(%q) = %d:%d, want %d:%d", tc.in, hh, mm, tc.hh, tc.mm)
		}
	}

	bad := []string{"", "14", "14:", ":05", "::", "25:00", "14:60", "abc:def", "14:0x"}
	for _, in := range bad {
		if _, _, err := parseHHMM(in); err == nil {
			t.Errorf("parseHHMM(%q) expected error, got nil", in)
		}
	}
}
