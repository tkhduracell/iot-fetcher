package extract

import (
	"bytes"
	"context"
	"fmt"
	"strings"

	ledongpdf "github.com/ledongthuc/pdf"
	"github.com/pdfcpu/pdfcpu/pkg/api"
	pdfmodel "github.com/pdfcpu/pdfcpu/pkg/pdfcpu/model"
)

// extractPDF implements the two-stage PDF pipeline:
//
//  1. ledongthuc text-layer extraction. If the average characters per page
//     meets the configured threshold, the text-layer output is returned.
//  2. Otherwise (scanned PDF) pdfcpu splits the file into N-page segments and
//     each segment is passed to the Flash Files API for OCR. Outputs are
//     concatenated in page order.
//
// Returns an error if the PDF is invalid, exceeds the page cap, or if the
// Flash fallback is required but no Flash client is configured.
func extractPDF(ctx context.Context, r *Router, fileHint string, body []byte) (string, error) {
	// Stage 1 — open via ledongthuc and try the text layer.
	reader, err := ledongpdf.NewReader(bytes.NewReader(body), int64(len(body)))
	if err != nil {
		return "", fmt.Errorf("extract: pdf %s: open: %w", fileHint, err)
	}
	pageCount := reader.NumPage()
	if pageCount <= 0 {
		return "", fmt.Errorf("extract: pdf %s: no pages", fileHint)
	}
	if pageCount > r.cfg.PDFMaxPages {
		return "", fmt.Errorf("extract: pdf %s has %d pages, exceeds cap %d",
			fileHint, pageCount, r.cfg.PDFMaxPages)
	}

	text, totalChars := readTextLayer(reader, pageCount)
	avgPerPage := totalChars / pageCount
	if avgPerPage >= r.cfg.PDFTextLayerMinCharsPerPage {
		return text, nil
	}

	// Stage 2 — Flash OCR fallback. Requires a Flash client.
	if r.cfg.Flash == nil {
		return "", fmt.Errorf(
			"extract: pdf %s has only %d chars across %d pages (avg %d < %d) and no flash client is configured",
			fileHint, totalChars, pageCount, avgPerPage, r.cfg.PDFTextLayerMinCharsPerPage,
		)
	}
	return ocrPDF(ctx, r, fileHint, body, pageCount)
}

// readTextLayer collects the plain-text layer from every page and returns the
// concatenated text plus the total character count. Per-page errors are
// swallowed — ledongthuc returns errors for e.g. pages with no usable fonts,
// but the caller just wants best-effort text plus a count to judge density.
func readTextLayer(reader *ledongpdf.Reader, pageCount int) (string, int) {
	var sb strings.Builder
	total := 0
	for i := 1; i <= pageCount; i++ {
		page := reader.Page(i)
		if page.V.IsNull() {
			continue
		}
		pageText, err := page.GetPlainText(nil)
		if err != nil {
			continue
		}
		total += len(pageText)
		sb.WriteString(pageText)
		if !strings.HasSuffix(pageText, "\n") {
			sb.WriteByte('\n')
		}
	}
	return sb.String(), total
}

// ocrPDF splits `body` into N-page segments via pdfcpu.Trim and passes each
// segment to Flash for OCR. Segment outputs are joined in page order.
func ocrPDF(ctx context.Context, r *Router, fileHint string, body []byte, pageCount int) (string, error) {
	ranges := splitPageRanges(pageCount, r.cfg.PDFMaxPagesPerCall)
	conf := pdfmodel.NewDefaultConfiguration()

	var out strings.Builder
	for idx, pageSpec := range ranges {
		var buf bytes.Buffer
		if err := api.Trim(bytes.NewReader(body), &buf, []string{pageSpec}, conf); err != nil {
			return "", fmt.Errorf("extract: pdf %s: pdfcpu trim %q: %w", fileHint, pageSpec, err)
		}
		hint := fmt.Sprintf("%s#pages=%s", fileHint, pageSpec)
		text, err := r.cfg.Flash.ExtractBytes(ctx, hint, "application/pdf", buf.Bytes())
		if err != nil {
			return "", fmt.Errorf("extract: pdf %s segment %s: %w", fileHint, pageSpec, err)
		}
		if idx > 0 && !strings.HasSuffix(out.String(), "\n") {
			out.WriteByte('\n')
		}
		out.WriteString(text)
	}
	return out.String(), nil
}

// splitPageRanges divides a PDF with `pageCount` pages into contiguous segments
// of at most `perCall` pages and returns the pdfcpu page-spec strings
// ("1-20", "21-40", ...). A single-page segment is rendered as "N" rather
// than "N-N" for clarity.
func splitPageRanges(pageCount, perCall int) []string {
	if perCall <= 0 {
		perCall = 20
	}
	var out []string
	for start := 1; start <= pageCount; start += perCall {
		end := start + perCall - 1
		if end > pageCount {
			end = pageCount
		}
		if start == end {
			out = append(out, fmt.Sprintf("%d", start))
		} else {
			out = append(out, fmt.Sprintf("%d-%d", start, end))
		}
	}
	return out
}

