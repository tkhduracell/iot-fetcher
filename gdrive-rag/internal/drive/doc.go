// Package drive wraps the Google Drive v3 API: listing whitelisted folders,
// resolving file metadata, downloading bytes, and tracking changes.
package drive

import (
	// Pin dependencies used by upcoming tasks so go mod tidy retains them
	// in go.mod during scaffolding.
	_ "google.golang.org/api/drive/v3"
	_ "google.golang.org/api/option"
)
