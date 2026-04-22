// Package extract pulls plain text out of source files: pure-Go PDF text
// extraction with a Gemini Flash fallback for scanned documents, direct
// Gemini OCR for images, and pass-through for text/markdown/CSV/JSON/source
// code.
//
// The dispatch entrypoint is Router.Extract, selecting the right pipeline by
// MIME type. Google-native formats (Docs, Sheets, Slides) must be exported to
// one of the pass-through MIME types (text/markdown, text/csv, text/plain) by
// the caller before reaching this package.
package extract
