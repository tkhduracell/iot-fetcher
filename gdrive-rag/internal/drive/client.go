// Package drive wraps the Google Drive v3 API: listing whitelisted folders,
// resolving file metadata, downloading bytes, and tracking changes.
package drive

import (
	"context"
	"fmt"
	"io"
	"sync"
	"time"

	"google.golang.org/api/drive/v3"
	"google.golang.org/api/option"
)

// fileFields is the field mask used whenever we fetch a File resource via
// files.get. Keeps payloads small.
const fileFields = "id, name, mimeType, modifiedTime, size, webViewLink, parents, trashed"

// Client wraps the Google Drive v3 API with an in-process cache of each
// file/folder's first parent, so ancestry lookups during changes-list
// processing don't re-hit the API for every file.
type Client struct {
	svc *drive.Service

	// parents maps fileID → first parent fileID. Drive rarely uses multi-parent
	// outside classic Team Drives, so we resolve ancestry through the first
	// parent only. A zero-value string means "no parent" (reached the root).
	parents map[string]string
	// names maps folderID → folder name, used when building AncestryPath.
	names     map[string]string
	parentsMu sync.RWMutex
}

// File is the trimmed metadata we need for indexing.
type File struct {
	ID           string
	Name         string
	MimeType     string
	ModifiedTime time.Time
	Size         int64 // 0 for Google-native docs (no binary size).
	WebViewLink  string
	Parents      []string
	Trashed      bool
}

// NewClient builds a Drive client from a service-account JSON blob with
// read-only scope.
func NewClient(ctx context.Context, serviceAccountJSON []byte) (*Client, error) {
	svc, err := drive.NewService(ctx,
		option.WithCredentialsJSON(serviceAccountJSON),
		option.WithScopes(drive.DriveReadonlyScope),
	)
	if err != nil {
		return nil, fmt.Errorf("drive.NewService: %w", err)
	}
	return &Client{
		svc:     svc,
		parents: make(map[string]string),
		names:   make(map[string]string),
	}, nil
}

// toFile converts a Drive API File to our trimmed File type.
func toFile(f *drive.File) *File {
	if f == nil {
		return nil
	}
	var modified time.Time
	if f.ModifiedTime != "" {
		if t, err := time.Parse(time.RFC3339, f.ModifiedTime); err == nil {
			modified = t
		}
	}
	return &File{
		ID:           f.Id,
		Name:         f.Name,
		MimeType:     f.MimeType,
		ModifiedTime: modified,
		Size:         f.Size,
		WebViewLink:  f.WebViewLink,
		Parents:      append([]string(nil), f.Parents...),
		Trashed:      f.Trashed,
	}
}

// GetFile fetches metadata for one file.
func (c *Client) GetFile(ctx context.Context, fileID string) (*File, error) {
	f, err := c.svc.Files.Get(fileID).
		Context(ctx).
		Fields(fileFields).
		SupportsAllDrives(true).
		Do()
	if err != nil {
		return nil, fmt.Errorf("files.get %s: %w", fileID, err)
	}
	return toFile(f), nil
}

// Export fetches Google-native docs as the requested MIME type (e.g.
// text/markdown for Docs, text/csv for Sheets, text/plain for Slides).
func (c *Client) Export(ctx context.Context, fileID, exportMime string) ([]byte, error) {
	resp, err := c.svc.Files.Export(fileID, exportMime).Context(ctx).Download()
	if err != nil {
		return nil, fmt.Errorf("files.export %s as %s: %w", fileID, exportMime, err)
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("read export body %s: %w", fileID, err)
	}
	return body, nil
}

// Download fetches the raw bytes of a binary file (PDF, image, text/*, etc.).
func (c *Client) Download(ctx context.Context, fileID string) ([]byte, error) {
	resp, err := c.svc.Files.Get(fileID).
		Context(ctx).
		SupportsAllDrives(true).
		Download()
	if err != nil {
		return nil, fmt.Errorf("files.get (download) %s: %w", fileID, err)
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("read download body %s: %w", fileID, err)
	}
	return body, nil
}
