package drive

import (
	"context"
	"testing"
)

// newTestClient returns a Client with no real Drive service but with the
// ancestry caches pre-populated. Tests exercise IsInWhitelist /
// AncestryPath through the cache; they must not hit c.svc.
func newTestClient(parents, names map[string]string) *Client {
	pCopy := make(map[string]string, len(parents))
	for k, v := range parents {
		pCopy[k] = v
	}
	nCopy := make(map[string]string, len(names))
	for k, v := range names {
		nCopy[k] = v
	}
	return &Client{
		svc:     nil, // MUST remain untouched in tests.
		parents: pCopy,
		names:   nCopy,
	}
}

func TestIsInWhitelist_DirectParent(t *testing.T) {
	// file -> folderA (whitelisted)
	c := newTestClient(map[string]string{
		"file1":   "folderA",
		"folderA": "",
	}, nil)
	ok, err := c.IsInWhitelist(context.Background(), "file1", []string{"folderA"})
	if err != nil {
		t.Fatalf("unexpected err: %v", err)
	}
	if !ok {
		t.Fatalf("expected file1 to be in whitelist via folderA")
	}
}

func TestIsInWhitelist_TransitiveAncestor(t *testing.T) {
	// file -> folderC -> folderB -> folderA (whitelisted)
	c := newTestClient(map[string]string{
		"file1":   "folderC",
		"folderC": "folderB",
		"folderB": "folderA",
		"folderA": "",
	}, nil)
	ok, err := c.IsInWhitelist(context.Background(), "file1", []string{"folderA"})
	if err != nil {
		t.Fatalf("unexpected err: %v", err)
	}
	if !ok {
		t.Fatalf("expected file1 to be in whitelist transitively")
	}
}

func TestIsInWhitelist_NotInWhitelist(t *testing.T) {
	// file -> folderC -> folderB -> root (nothing whitelisted on this path)
	c := newTestClient(map[string]string{
		"file1":   "folderC",
		"folderC": "folderB",
		"folderB": "",
	}, nil)
	ok, err := c.IsInWhitelist(context.Background(), "file1", []string{"folderA", "folderZ"})
	if err != nil {
		t.Fatalf("unexpected err: %v", err)
	}
	if ok {
		t.Fatalf("expected file1 to NOT be in whitelist")
	}
}

func TestIsInWhitelist_EmptyWhitelist(t *testing.T) {
	c := newTestClient(map[string]string{"file1": "folderA", "folderA": ""}, nil)
	ok, err := c.IsInWhitelist(context.Background(), "file1", nil)
	if err != nil {
		t.Fatalf("unexpected err: %v", err)
	}
	if ok {
		t.Fatalf("empty whitelist must never match")
	}
}

func TestIsInWhitelist_OneOfMany(t *testing.T) {
	// file -> folderC -> folderB -> folderA (only folderB whitelisted)
	c := newTestClient(map[string]string{
		"file1":   "folderC",
		"folderC": "folderB",
		"folderB": "folderA",
		"folderA": "",
	}, nil)
	ok, err := c.IsInWhitelist(context.Background(), "file1", []string{"nope", "folderB", "other"})
	if err != nil {
		t.Fatalf("unexpected err: %v", err)
	}
	if !ok {
		t.Fatalf("expected match via folderB")
	}
}

func TestIsInWhitelist_CycleSafety(t *testing.T) {
	// Should not happen in Drive, but we must not infinite-loop.
	c := newTestClient(map[string]string{
		"a": "b",
		"b": "a",
	}, nil)
	ok, err := c.IsInWhitelist(context.Background(), "a", []string{"z"})
	if err != nil {
		t.Fatalf("unexpected err: %v", err)
	}
	if ok {
		t.Fatalf("cycle must return false without erroring")
	}
}

func TestIsInWhitelist_CacheAvoidsRealCall(t *testing.T) {
	// c.svc is nil — this test passing proves we never dereferenced it.
	c := newTestClient(map[string]string{
		"file1":   "folderA",
		"folderA": "",
	}, nil)
	if _, err := c.IsInWhitelist(context.Background(), "file1", []string{"folderA"}); err != nil {
		t.Fatalf("unexpected err: %v", err)
	}
}

func TestAncestryPath_Basic(t *testing.T) {
	// file1 -> Reports (child) -> Q1 (child) -> Root (whitelisted)
	c := newTestClient(
		map[string]string{
			"file1":  "q1",
			"q1":     "reports",
			"reports": "root",
			"root":   "",
		},
		map[string]string{
			"q1":      "Q1",
			"reports": "Reports",
			"root":    "Root",
		},
	)
	path, err := c.AncestryPath(context.Background(), "file1", []string{"root"})
	if err != nil {
		t.Fatalf("unexpected err: %v", err)
	}
	want := "Root/Reports/Q1"
	if path != want {
		t.Fatalf("got %q, want %q", path, want)
	}
}

func TestAncestryPath_DirectParentIsWhitelist(t *testing.T) {
	// file1's immediate parent IS the whitelisted folder.
	c := newTestClient(
		map[string]string{
			"file1": "root",
			"root":  "",
		},
		map[string]string{
			"root": "Root",
		},
	)
	path, err := c.AncestryPath(context.Background(), "file1", []string{"root"})
	if err != nil {
		t.Fatalf("unexpected err: %v", err)
	}
	if path != "Root" {
		t.Fatalf("got %q, want %q", path, "Root")
	}
}

func TestAncestryPath_NotInWhitelist(t *testing.T) {
	c := newTestClient(
		map[string]string{
			"file1": "folderA",
			"folderA": "",
		},
		map[string]string{"folderA": "A"},
	)
	path, err := c.AncestryPath(context.Background(), "file1", []string{"other"})
	if err != nil {
		t.Fatalf("unexpected err: %v", err)
	}
	if path != "" {
		t.Fatalf("expected empty path, got %q", path)
	}
}

func TestAncestryPath_EmptyWhitelist(t *testing.T) {
	c := newTestClient(
		map[string]string{"file1": "folderA", "folderA": ""},
		map[string]string{"folderA": "A"},
	)
	path, err := c.AncestryPath(context.Background(), "file1", nil)
	if err != nil {
		t.Fatalf("unexpected err: %v", err)
	}
	if path != "" {
		t.Fatalf("expected empty path, got %q", path)
	}
}

func TestAncestryPath_StopsAtFirstWhitelistedAncestor(t *testing.T) {
	// Chain: file1 -> inner -> outer (both whitelisted). The NEAREST
	// whitelisted ancestor becomes the root — we do not continue up past it.
	c := newTestClient(
		map[string]string{
			"file1": "inner",
			"inner": "outer",
			"outer": "",
		},
		map[string]string{
			"inner": "Inner",
			"outer": "Outer",
		},
	)
	path, err := c.AncestryPath(context.Background(), "file1", []string{"inner", "outer"})
	if err != nil {
		t.Fatalf("unexpected err: %v", err)
	}
	if path != "Inner" {
		t.Fatalf("got %q, want %q", path, "Inner")
	}
}

func TestResolveParent_UsesCache(t *testing.T) {
	// With a nil svc, a cached parent must return without panicking.
	c := newTestClient(map[string]string{"f": "p"}, nil)
	p, err := c.resolveParent(context.Background(), "f")
	if err != nil {
		t.Fatalf("unexpected err: %v", err)
	}
	if p != "p" {
		t.Fatalf("got %q, want %q", p, "p")
	}
}
