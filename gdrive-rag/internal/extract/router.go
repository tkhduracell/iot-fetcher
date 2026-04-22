package extract

import (
	"context"
	"errors"
	"fmt"
	"strings"
)

// ErrUnsupported is returned when the MIME type isn't handled by any extractor.
// Callers log and skip the file.
var ErrUnsupported = errors.New("extract: unsupported mime type")

// flashExtractor is the narrow interface that Router calls on a FlashClient.
// Extracted as an interface so tests can inject a fake without hitting the real
// Gemini API.
type flashExtractor interface {
	ExtractBytes(ctx context.Context, fileHint, mimeHint string, body []byte) (string, error)
}

// Config parameterises the router. Zero values fall back to sensible defaults
// so callers can leave most fields unset.
type Config struct {
	// Flash is the Gemini Flash client (see gemini_flash.go). nil disables OCR
	// paths: PDFs without a text layer and images will return an error.
	Flash flashExtractor

	// PDFTextLayerMinCharsPerPage is the threshold below which a PDF is treated
	// as scanned and falls through to the Flash OCR path. Default 100.
	PDFTextLayerMinCharsPerPage int

	// PDFMaxPagesPerCall is the number of pages per Flash OCR request when
	// splitting a scanned PDF. Default 20.
	PDFMaxPagesPerCall int

	// PDFMaxPages is the hard ceiling for PDFs. Files with more pages are
	// rejected before any extraction work. Default 500.
	PDFMaxPages int

	// SkipImages, when true, makes image/* MIME types return ErrUnsupported
	// without calling Flash.
	SkipImages bool
}

// Router dispatches extraction work by MIME type.
type Router struct {
	cfg Config
}

// NewRouter builds a Router, applying defaults for unset Config fields.
func NewRouter(cfg Config) *Router {
	if cfg.PDFTextLayerMinCharsPerPage <= 0 {
		cfg.PDFTextLayerMinCharsPerPage = 100
	}
	if cfg.PDFMaxPagesPerCall <= 0 {
		cfg.PDFMaxPagesPerCall = 20
	}
	if cfg.PDFMaxPages <= 0 {
		cfg.PDFMaxPages = 500
	}
	return &Router{cfg: cfg}
}

// Extract returns the extracted UTF-8 text for the given bytes and MIME type.
// The fileHint (e.g. filename or "fileID") is used in Flash prompts / error
// messages for observability.
//
// Returns ErrUnsupported for MIME types this extractor doesn't handle. Returns
// budget.ErrDailyBudgetExhausted if a Flash call would exceed the daily cap.
func (r *Router) Extract(ctx context.Context, mimeType, fileHint string, body []byte) (string, error) {
	mime := normalizeMIME(mimeType)

	switch {
	case mime == "application/pdf":
		return extractPDF(ctx, r, fileHint, body)

	case strings.HasPrefix(mime, "image/"):
		if r.cfg.SkipImages {
			return "", fmt.Errorf("%w: images disabled (mime=%s)", ErrUnsupported, mime)
		}
		if r.cfg.Flash == nil {
			return "", fmt.Errorf("extract: flash client is required for image OCR (mime=%s)", mime)
		}
		return r.cfg.Flash.ExtractBytes(ctx, fileHint, mime, body)

	case isTextualMIME(mime):
		return stripBOM(string(body)), nil

	default:
		return "", fmt.Errorf("%w: mime=%q", ErrUnsupported, mime)
	}
}

// normalizeMIME lower-cases a MIME type and strips parameters like "; charset=".
func normalizeMIME(m string) string {
	m = strings.TrimSpace(m)
	if i := strings.Index(m, ";"); i >= 0 {
		m = m[:i]
	}
	return strings.ToLower(strings.TrimSpace(m))
}

// stripBOM removes a leading UTF-8 BOM.
func stripBOM(s string) string {
	return strings.TrimPrefix(s, "\ufeff")
}

// isTextualMIME reports whether the MIME type can be passed through as UTF-8
// text without any real extraction work.
func isTextualMIME(mime string) bool {
	if strings.HasPrefix(mime, "text/") {
		return true
	}
	switch mime {
	case
		"application/json",
		"application/ld+json",
		"application/xml",
		"application/javascript",
		"application/ecmascript",
		"application/x-yaml",
		"application/yaml",
		"application/x-sh",
		"application/x-shellscript",
		"application/x-python",
		"application/x-ruby",
		"application/x-perl",
		"application/x-tex",
		"application/x-latex",
		"application/toml",
		"application/x-toml",
		"application/sql",
		"application/x-sql":
		return true
	}
	return false
}
