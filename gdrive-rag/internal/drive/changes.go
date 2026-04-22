package drive

import (
	"context"
	"fmt"
	"strings"

	"google.golang.org/api/drive/v3"
)

// mimeTypeFolder is the MIME type for Drive folders.
const mimeTypeFolder = "application/vnd.google-apps.folder"

// changesFields is the field mask for changes.list calls.
const changesFields = "nextPageToken, newStartPageToken, changes(fileId, removed, file(" + fileFields + "))"

// Change represents one entry from changes.list.
type Change struct {
	FileID  string
	Removed bool
	File    *File // nil if Removed
}

// GetStartPageToken bootstraps the changes.list cursor.
func (c *Client) GetStartPageToken(ctx context.Context) (string, error) {
	tok, err := c.svc.Changes.GetStartPageToken().
		Context(ctx).
		SupportsAllDrives(true).
		Do()
	if err != nil {
		return "", fmt.Errorf("changes.getStartPageToken: %w", err)
	}
	return tok.StartPageToken, nil
}

// ListChanges returns all changes since pageToken, paging internally. It
// returns the new pageToken to persist (Drive returns this in
// newStartPageToken on the last page).
func (c *Client) ListChanges(ctx context.Context, pageToken string) ([]Change, string, error) {
	var out []Change
	newToken := pageToken
	current := pageToken
	for {
		call := c.svc.Changes.List(current).
			Context(ctx).
			Fields(changesFields).
			IncludeRemoved(true).
			IncludeItemsFromAllDrives(true).
			SupportsAllDrives(true)
		resp, err := call.Do()
		if err != nil {
			return nil, "", fmt.Errorf("changes.list pageToken=%q: %w", current, err)
		}
		for _, ch := range resp.Changes {
			// Ignore change-type==drive events; we only care about files.
			if ch.FileId == "" {
				continue
			}
			entry := Change{
				FileID:  ch.FileId,
				Removed: ch.Removed,
			}
			if !ch.Removed && ch.File != nil {
				entry.File = toFile(ch.File)
				// Seed the ancestry cache from the fresh metadata.
				c.rememberFileMetadata(ch.File)
			}
			out = append(out, entry)
		}
		if resp.NextPageToken != "" {
			current = resp.NextPageToken
			continue
		}
		if resp.NewStartPageToken != "" {
			newToken = resp.NewStartPageToken
		}
		break
	}
	return out, newToken, nil
}

// rememberFileMetadata seeds the parents and names caches when we already
// have a File struct in hand (e.g. from changes.list or files.list).
func (c *Client) rememberFileMetadata(f *drive.File) {
	if f == nil || f.Id == "" {
		return
	}
	c.parentsMu.Lock()
	defer c.parentsMu.Unlock()
	if len(f.Parents) > 0 {
		c.parents[f.Id] = f.Parents[0]
	}
	if f.Name != "" {
		c.names[f.Id] = f.Name
	}
}

// resolveParent returns the parent fileID of fileID. Uses the cache; on miss,
// calls Drive's files.get with fields=parents,name. Injectable in tests via
// the cache (pre-populate c.parents/c.names so the live svc is never touched).
// Returns an empty string (no error) when the file has no parents (reached
// the root of the Drive).
func (c *Client) resolveParent(ctx context.Context, fileID string) (string, error) {
	c.parentsMu.RLock()
	if p, ok := c.parents[fileID]; ok {
		c.parentsMu.RUnlock()
		return p, nil
	}
	c.parentsMu.RUnlock()

	f, err := c.svc.Files.Get(fileID).
		Context(ctx).
		Fields("id, name, parents").
		SupportsAllDrives(true).
		Do()
	if err != nil {
		return "", fmt.Errorf("files.get parents %s: %w", fileID, err)
	}

	c.parentsMu.Lock()
	defer c.parentsMu.Unlock()
	if f.Name != "" {
		c.names[f.Id] = f.Name
	}
	if len(f.Parents) == 0 {
		c.parents[fileID] = ""
		return "", nil
	}
	p := f.Parents[0]
	c.parents[fileID] = p
	return p, nil
}

// resolveName returns the display name for a fileID. Uses the names cache;
// on miss, calls files.get.
func (c *Client) resolveName(ctx context.Context, fileID string) (string, error) {
	c.parentsMu.RLock()
	if n, ok := c.names[fileID]; ok {
		c.parentsMu.RUnlock()
		return n, nil
	}
	c.parentsMu.RUnlock()

	f, err := c.svc.Files.Get(fileID).
		Context(ctx).
		Fields("id, name, parents").
		SupportsAllDrives(true).
		Do()
	if err != nil {
		return "", fmt.Errorf("files.get name %s: %w", fileID, err)
	}
	c.parentsMu.Lock()
	defer c.parentsMu.Unlock()
	c.names[f.Id] = f.Name
	if len(f.Parents) > 0 {
		c.parents[f.Id] = f.Parents[0]
	} else if _, ok := c.parents[f.Id]; !ok {
		c.parents[f.Id] = ""
	}
	return f.Name, nil
}

// IsInWhitelist returns true if fileID has any of whitelistedFolderIDs in its
// transitive ancestor chain. Uses the in-process parents cache; on cache miss,
// calls Drive's files.get with fields=parents.
//
// Returns an error only for Drive API failures; returns false with nil error
// when the file simply isn't in any whitelisted folder.
func (c *Client) IsInWhitelist(ctx context.Context, fileID string, whitelistedFolderIDs []string) (bool, error) {
	if len(whitelistedFolderIDs) == 0 {
		return false, nil
	}
	whitelist := make(map[string]struct{}, len(whitelistedFolderIDs))
	for _, id := range whitelistedFolderIDs {
		whitelist[id] = struct{}{}
	}

	// Walk ancestry. The fileID itself is not tested against whitelist (we
	// only care about folder ancestry). Guard against cycles with a seen set.
	seen := make(map[string]struct{})
	cur := fileID
	for {
		if _, ok := seen[cur]; ok {
			// Cycle — should not happen in Drive, but avoid infinite loop.
			return false, nil
		}
		seen[cur] = struct{}{}

		parent, err := c.resolveParent(ctx, cur)
		if err != nil {
			return false, err
		}
		if parent == "" {
			return false, nil
		}
		if _, hit := whitelist[parent]; hit {
			return true, nil
		}
		cur = parent
	}
}

// AncestryPath builds a "/"-joined folder-name path from the whitelisted root
// down to the file's immediate parent, for use as metadata (folder_path).
// Returns empty string if not inside any whitelist.
func (c *Client) AncestryPath(ctx context.Context, fileID string, whitelistedFolderIDs []string) (string, error) {
	if len(whitelistedFolderIDs) == 0 {
		return "", nil
	}
	whitelist := make(map[string]struct{}, len(whitelistedFolderIDs))
	for _, id := range whitelistedFolderIDs {
		whitelist[id] = struct{}{}
	}

	// Collect the chain of ancestor folder IDs, stopping as soon as we hit a
	// whitelisted folder (which becomes the root of the returned path).
	var chain []string
	seen := make(map[string]struct{})
	cur := fileID
	for {
		if _, ok := seen[cur]; ok {
			return "", nil
		}
		seen[cur] = struct{}{}

		parent, err := c.resolveParent(ctx, cur)
		if err != nil {
			return "", err
		}
		if parent == "" {
			// Reached root without finding a whitelisted folder.
			return "", nil
		}
		chain = append(chain, parent)
		if _, hit := whitelist[parent]; hit {
			break
		}
		cur = parent
	}

	// chain is ordered [immediate-parent, ..., whitelisted-root]. Reverse and
	// resolve names.
	names := make([]string, 0, len(chain))
	for i := len(chain) - 1; i >= 0; i-- {
		n, err := c.resolveName(ctx, chain[i])
		if err != nil {
			return "", err
		}
		names = append(names, n)
	}
	return strings.Join(names, "/"), nil
}

// ListFolder recursively lists all non-trashed, non-folder files under the
// given folder (and its subfolders). Calls yield for each file; returning an
// error from yield stops iteration and propagates the error.
func (c *Client) ListFolder(ctx context.Context, folderID string, yield func(*File) error) error {
	q := fmt.Sprintf("'%s' in parents and trashed=false", folderID)
	listCall := c.svc.Files.List().
		Context(ctx).
		Q(q).
		Fields("nextPageToken, files(" + fileFields + ")").
		PageSize(1000).
		SupportsAllDrives(true).
		IncludeItemsFromAllDrives(true)

	pageToken := ""
	for {
		call := listCall
		if pageToken != "" {
			call = call.PageToken(pageToken)
		}
		resp, err := call.Do()
		if err != nil {
			return fmt.Errorf("files.list folder=%s: %w", folderID, err)
		}
		for _, f := range resp.Files {
			// Seed caches so later ancestry/name lookups are free.
			c.rememberFileMetadata(f)
			if f.MimeType == mimeTypeFolder {
				if err := c.ListFolder(ctx, f.Id, yield); err != nil {
					return err
				}
				continue
			}
			if err := yield(toFile(f)); err != nil {
				return err
			}
		}
		if resp.NextPageToken == "" {
			break
		}
		pageToken = resp.NextPageToken
	}
	return nil
}
