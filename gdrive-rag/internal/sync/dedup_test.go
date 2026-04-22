package sync

import "testing"

func TestNeedsEmbed(t *testing.T) {
	tests := []struct {
		name     string
		hashes   []string
		existing map[int]string
		want     bool
	}{
		{
			name:     "same count same hashes → false",
			hashes:   []string{"a", "b", "c"},
			existing: map[int]string{0: "a", 1: "b", 2: "c"},
			want:     false,
		},
		{
			name:     "same count different hash → true",
			hashes:   []string{"a", "b", "c"},
			existing: map[int]string{0: "a", 1: "X", 2: "c"},
			want:     true,
		},
		{
			name:     "fewer existing chunks → true",
			hashes:   []string{"a", "b", "c"},
			existing: map[int]string{0: "a", 1: "b"},
			want:     true,
		},
		{
			name:     "more existing chunks → true",
			hashes:   []string{"a", "b"},
			existing: map[int]string{0: "a", 1: "b", 2: "c"},
			want:     true,
		},
		{
			name:     "both empty → false",
			hashes:   nil,
			existing: map[int]string{},
			want:     false,
		},
		{
			name:     "new non-empty vs empty existing → true",
			hashes:   []string{"a"},
			existing: map[int]string{},
			want:     true,
		},
		{
			name:     "empty hashes vs non-empty existing → true",
			hashes:   nil,
			existing: map[int]string{0: "a"},
			want:     true,
		},
		{
			name:     "missing index in existing with matching count is impossible but safe → true",
			hashes:   []string{"a", "b"},
			existing: map[int]string{0: "a", 5: "b"}, // len matches but index 1 is empty
			want:     true,
		},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			got := needsEmbed(tc.hashes, tc.existing)
			if got != tc.want {
				t.Errorf("needsEmbed(%v, %v) = %v; want %v", tc.hashes, tc.existing, got, tc.want)
			}
		})
	}
}
