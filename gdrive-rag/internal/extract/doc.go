// Package extract pulls plain text out of source files: pure-Go PDF text
// extraction with a Gemini Flash fallback for scanned documents.
package extract

import (
	// Pin dependencies used by upcoming tasks so go mod tidy retains them
	// in go.mod during scaffolding.
	_ "github.com/ledongthuc/pdf"
	_ "github.com/pdfcpu/pdfcpu/pkg/api"
)
