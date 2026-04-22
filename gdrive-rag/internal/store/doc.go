// Package store wraps the chromem-go embedded vector database for persistent
// chunk storage and similarity search.
package store

import (
	// Pin dependencies used by upcoming tasks so go mod tidy retains them
	// in go.mod during scaffolding.
	_ "github.com/philippgille/chromem-go"
)
