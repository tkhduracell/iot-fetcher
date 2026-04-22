package chunk

import (
	"strings"
	"testing"
	"unicode"
)

func TestSplitEmpty(t *testing.T) {
	if got := Split("", 100, 10); got != nil {
		t.Errorf("empty input: got %+v, want nil", got)
	}
}

func TestSplitShort(t *testing.T) {
	// Short text (< targetTokens worth of runes) => single chunk.
	text := "hello world, a short fragment."
	got := Split(text, 100, 10) // targetRunes = 400 >> len
	if len(got) != 1 {
		t.Fatalf("want 1 chunk, got %d", len(got))
	}
	if got[0].Index != 0 || got[0].Text != text {
		t.Errorf("chunk[0]: got %+v", got[0])
	}
}

func TestSplitLongIndexesAndOverlap(t *testing.T) {
	// Build a long string (1000 runes) with no whitespace so we always cut
	// at the rune boundary -> overlap is deterministic.
	text := strings.Repeat("x", 1000)

	// targetTokens=100 => targetRunes=400, overlapTokens=25 => overlapRunes=100.
	got := Split(text, 100, 25)
	if len(got) < 2 {
		t.Fatalf("want >= 2 chunks for 1000 runes, got %d", len(got))
	}
	for i, c := range got {
		if c.Index != i {
			t.Errorf("chunk[%d].Index = %d, want %d", i, c.Index, i)
		}
	}
	// Re-assembling by dropping overlap prefixes from non-first chunks must
	// reconstruct the full text.
	var rebuilt strings.Builder
	rebuilt.WriteString(got[0].Text)
	prevLen := len([]rune(got[0].Text))
	for i := 1; i < len(got); i++ {
		cur := []rune(got[i].Text)
		// Overlap is min(overlapRunes, prevLen).
		ov := 100
		if prevLen < ov {
			ov = prevLen
		}
		if len(cur) <= ov {
			// Final tiny tail: append fully.
			rebuilt.WriteString(got[i].Text)
		} else {
			rebuilt.WriteString(string(cur[ov:]))
		}
		prevLen = len(cur)
	}
	if rebuilt.String() != text {
		t.Errorf("reassembly mismatch: got len=%d want len=%d", len(rebuilt.String()), len(text))
	}
}

func TestSplitPrefersWhitespaceBoundary(t *testing.T) {
	// Build text that has a well-placed space just before the target cut
	// (end = targetTokens*4 = 400). The splitter should cut at that space,
	// not mid-word.
	prefix := strings.Repeat("a", 395)  // 395 non-space runes
	suffix := strings.Repeat("b", 1000) // forces more than one chunk
	text := prefix + " " + suffix       // space at index 395

	got := Split(text, 100, 10)
	if len(got) < 2 {
		t.Fatalf("want >= 2 chunks, got %d", len(got))
	}
	// First chunk must end at the whitespace (index 395), so the last rune
	// of chunk[0] must NOT be 'b' (mid-word cut) — it should be 'a'.
	first := []rune(got[0].Text)
	last := first[len(first)-1]
	if last == 'b' {
		t.Errorf("first chunk cut mid-word; last rune = %q", last)
	}
	// And the cut point should be the space -> first chunk length == 395,
	// i.e. everything before the space (exclusive of it).
	if len(first) != 395 {
		t.Errorf("expected whitespace cut at rune 395, got chunk len %d", len(first))
	}
	if !unicode.IsSpace(rune(text[395])) {
		t.Fatal("test precondition: index 395 must be whitespace")
	}
}

func TestSplitZeroOverlap(t *testing.T) {
	text := strings.Repeat("x", 800) // 2 chunks at targetRunes=400
	got := Split(text, 100, 0)
	if len(got) != 2 {
		t.Fatalf("want 2 chunks, got %d", len(got))
	}
	if got[0].Text+got[1].Text != text {
		t.Errorf("zero-overlap reassembly mismatch")
	}
}

func TestSplitOverlapClamped(t *testing.T) {
	// overlap >= target is clamped to target-1; must still make forward progress.
	text := strings.Repeat("x", 2000)
	got := Split(text, 10, 100)
	if len(got) == 0 {
		t.Fatal("got no chunks")
	}
	// Forward-progress check: at least one chunk and final covers the end.
	last := got[len(got)-1]
	if !strings.HasSuffix(text, last.Text) {
		t.Errorf("final chunk doesn't cover end of input")
	}
}

func TestSplitNonASCIIRunes(t *testing.T) {
	// Use multibyte runes to confirm rune-count is correct, not byte-count.
	text := strings.Repeat("ä", 500) // 500 runes, 1000 bytes
	got := Split(text, 100, 10)      // targetRunes=400
	if len(got) < 2 {
		t.Fatalf("want >= 2 chunks for 500 runes, got %d", len(got))
	}
}

func TestSplitNegativeOverlap(t *testing.T) {
	text := strings.Repeat("x", 500)
	got := Split(text, 100, -5)
	if len(got) == 0 {
		t.Fatal("want >= 1 chunk")
	}
}

func TestSplitZeroTarget(t *testing.T) {
	got := Split("hello", 0, 0)
	if len(got) != 1 || got[0].Text != "hello" {
		t.Errorf("zero target should return single chunk; got %+v", got)
	}
}
