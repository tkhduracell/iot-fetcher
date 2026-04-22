// Package embed wraps the Gemini embeddings API with batching and rate-limit
// awareness so the sync loop can translate chunks into vectors.
package embed

import (
	// Pin dependencies used by upcoming tasks so go mod tidy retains them
	// in go.mod during scaffolding.
	_ "google.golang.org/genai"
)
