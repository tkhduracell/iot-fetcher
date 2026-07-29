package extract

import (
	"bytes"
	"context"
	"fmt"
	"strings"
	"testing"
)

// buildMinimalPDF returns a hand-crafted single-page PDF containing `text`
// in the Helvetica Type1 font. It's the smallest thing the ledongthuc/pdf
// reader will accept as a valid PDF with a text layer.
//
// The byte offsets in the xref table are computed after the body is written.
// Any change to the body layout requires recomputing them, so this helper is
// constrained to a single text string drawn at (72, 720).
func buildMinimalPDF(text string) []byte {
	// Escape parens in the text literal per PDF string grammar.
	escaped := strings.NewReplacer(`\`, `\\`, `(`, `\(`, `)`, `\)`).Replace(text)

	var buf bytes.Buffer
	// PDF header — must start at byte 0.
	buf.WriteString("%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")

	// Object offsets (0 is the free-list head, must stay at 0).
	offsets := []int{0}

	// Object 1: Catalog.
	offsets = append(offsets, buf.Len())
	buf.WriteString("1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")

	// Object 2: Pages.
	offsets = append(offsets, buf.Len())
	buf.WriteString("2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n")

	// Object 3: Page.
	offsets = append(offsets, buf.Len())
	buf.WriteString("3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] " +
		"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>\nendobj\n")

	// Object 4: Content stream.
	content := fmt.Sprintf("BT /F1 24 Tf 72 720 Td (%s) Tj ET\n", escaped)
	offsets = append(offsets, buf.Len())
	fmt.Fprintf(&buf, "4 0 obj\n<< /Length %d >>\nstream\n%sendstream\nendobj\n",
		len(content), content)

	// Object 5: Font.
	offsets = append(offsets, buf.Len())
	buf.WriteString("5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n")

	// xref
	xrefOffset := buf.Len()
	fmt.Fprintf(&buf, "xref\n0 %d\n", len(offsets))
	// Free-list head (object 0).
	buf.WriteString("0000000000 65535 f \n")
	for i := 1; i < len(offsets); i++ {
		fmt.Fprintf(&buf, "%010d 00000 n \n", offsets[i])
	}

	// trailer
	fmt.Fprintf(&buf,
		"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n",
		len(offsets), xrefOffset,
	)
	return buf.Bytes()
}

func TestExtractPDF_TextLayer(t *testing.T) {
	r := NewRouter(Config{PDFTextLayerMinCharsPerPage: 5})
	body := buildMinimalPDF("Hello, World")

	got, err := r.Extract(context.Background(), "application/pdf", "test.pdf", body)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !strings.Contains(got, "Hello, World") {
		t.Fatalf("expected text-layer output to contain %q, got %q", "Hello, World", got)
	}
}

func TestExtractPDF_TextLayerBelowThreshold_FallsBackToFlash(t *testing.T) {
	// The PDF has ~12 chars on one page. Threshold of 1000 forces the fallback.
	ff := &fakeFlash{reply: "flash-extracted-text"}
	r := NewRouter(Config{
		PDFTextLayerMinCharsPerPage: 1000,
		PDFMaxPagesPerCall:          20,
		Flash:                       ff,
	})
	body := buildMinimalPDF("Hi")

	got, err := r.Extract(context.Background(), "application/pdf", "scanned.pdf", body)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got != "flash-extracted-text" {
		t.Fatalf("got %q, want %q", got, "flash-extracted-text")
	}
	if len(ff.calls) == 0 {
		t.Fatal("expected Flash to be called for the fallback path")
	}
	if ff.calls[0].mime != "application/pdf" {
		t.Fatalf("fallback flash call mime = %q, want application/pdf", ff.calls[0].mime)
	}
}

func TestExtractPDF_NoFlashFallback(t *testing.T) {
	r := NewRouter(Config{
		PDFTextLayerMinCharsPerPage: 1000, // force fallback
		Flash:                       nil,
	})
	body := buildMinimalPDF("x")
	_, err := r.Extract(context.Background(), "application/pdf", "scanned.pdf", body)
	if err == nil {
		t.Fatal("expected error when fallback is needed and no Flash is configured")
	}
}

func TestExtractPDF_MaxPagesRejected(t *testing.T) {
	// A 1-page PDF against PDFMaxPages=0-after-defaults means cap=500 by default.
	// Simulate "too many pages" by using a custom router with PDFMaxPages set to
	// a value below 1; NewRouter forces the default, so set it via struct after.
	r := &Router{cfg: Config{
		PDFTextLayerMinCharsPerPage: 100,
		PDFMaxPagesPerCall:          20,
		PDFMaxPages:                 0, // any page count exceeds this
	}}
	body := buildMinimalPDF("one")
	_, err := r.Extract(context.Background(), "application/pdf", "big.pdf", body)
	if err == nil {
		t.Fatal("expected error when pageCount > PDFMaxPages")
	}
	if !strings.Contains(err.Error(), "exceeds cap") {
		t.Fatalf("expected 'exceeds cap' in error, got %v", err)
	}
}

func TestSplitPageRanges(t *testing.T) {
	tests := []struct {
		pages, perCall int
		want           []string
	}{
		{5, 20, []string{"1-5"}},
		{20, 20, []string{"1-20"}},
		{50, 20, []string{"1-20", "21-40", "41-50"}},
		{1, 20, []string{"1"}},
		{21, 20, []string{"1-20", "21"}},
	}
	for _, tc := range tests {
		got := splitPageRanges(tc.pages, tc.perCall)
		if len(got) != len(tc.want) {
			t.Errorf("splitPageRanges(%d,%d) got %v, want %v", tc.pages, tc.perCall, got, tc.want)
			continue
		}
		for i := range got {
			if got[i] != tc.want[i] {
				t.Errorf("splitPageRanges(%d,%d)[%d] = %q, want %q",
					tc.pages, tc.perCall, i, got[i], tc.want[i])
			}
		}
	}
}

func TestExtractPDF_InvalidBytes(t *testing.T) {
	r := NewRouter(Config{})
	_, err := r.Extract(context.Background(), "application/pdf", "junk.pdf", []byte("not a pdf"))
	if err == nil {
		t.Fatal("expected error on garbage bytes")
	}
}
