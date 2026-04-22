package extract

import (
	"context"
	"errors"
	"testing"
)

// fakeFlash records the arguments to ExtractBytes and returns a fixed response.
type fakeFlash struct {
	calls []fakeFlashCall
	reply string
	err   error
}

type fakeFlashCall struct {
	hint string
	mime string
	body []byte
}

func (f *fakeFlash) ExtractBytes(_ context.Context, hint, mime string, body []byte) (string, error) {
	// Copy body so callers can reuse the buffer without racing with us.
	cp := make([]byte, len(body))
	copy(cp, body)
	f.calls = append(f.calls, fakeFlashCall{hint: hint, mime: mime, body: cp})
	return f.reply, f.err
}

func TestRouter_TextPassThrough(t *testing.T) {
	r := NewRouter(Config{})
	ctx := context.Background()

	cases := []struct {
		name string
		mime string
		body string
		want string
	}{
		{"plain", "text/plain", "hello world\n", "hello world\n"},
		{"markdown", "text/markdown; charset=utf-8", "# Title\n\nbody", "# Title\n\nbody"},
		{"csv", "text/csv", "a,b\n1,2\n", "a,b\n1,2\n"},
		{"json", "application/json", `{"k":"v"}`, `{"k":"v"}`},
		{"xml", "application/xml", "<root/>", "<root/>"},
		{"javascript", "application/javascript", "console.log(1)", "console.log(1)"},
		{"yaml", "application/x-yaml", "k: v", "k: v"},
		{"bom-stripped", "text/plain", "\ufeffhi", "hi"},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got, err := r.Extract(ctx, tc.mime, "hint", []byte(tc.body))
			if err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
			if got != tc.want {
				t.Fatalf("got %q, want %q", got, tc.want)
			}
		})
	}
}

func TestRouter_UnsupportedMIME(t *testing.T) {
	r := NewRouter(Config{})
	_, err := r.Extract(context.Background(), "application/vnd.ms-excel", "hint", []byte("junk"))
	if !errors.Is(err, ErrUnsupported) {
		t.Fatalf("want ErrUnsupported, got %v", err)
	}
}

func TestRouter_ImageSkip(t *testing.T) {
	r := NewRouter(Config{SkipImages: true, Flash: &fakeFlash{reply: "should-not-be-called"}})
	_, err := r.Extract(context.Background(), "image/png", "hint", []byte{0x89, 0x50, 0x4e, 0x47})
	if !errors.Is(err, ErrUnsupported) {
		t.Fatalf("want ErrUnsupported with SkipImages, got %v", err)
	}
}

func TestRouter_ImageNoFlash(t *testing.T) {
	r := NewRouter(Config{Flash: nil})
	_, err := r.Extract(context.Background(), "image/jpeg", "hint", []byte{0xff, 0xd8})
	if err == nil {
		t.Fatal("want error when Flash is nil, got nil")
	}
	if errors.Is(err, ErrUnsupported) {
		t.Fatalf("want a non-unsupported error (missing client), got ErrUnsupported: %v", err)
	}
}

func TestRouter_ImageViaFlash(t *testing.T) {
	ff := &fakeFlash{reply: "extracted image text"}
	r := NewRouter(Config{Flash: ff})
	got, err := r.Extract(context.Background(), "image/png", "hint", []byte{0x89, 0x50})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got != "extracted image text" {
		t.Fatalf("got %q, want %q", got, "extracted image text")
	}
	if len(ff.calls) != 1 {
		t.Fatalf("expected exactly one flash call, got %d", len(ff.calls))
	}
	if ff.calls[0].mime != "image/png" {
		t.Fatalf("flash called with mime %q, want image/png", ff.calls[0].mime)
	}
	if ff.calls[0].hint != "hint" {
		t.Fatalf("flash called with hint %q, want hint", ff.calls[0].hint)
	}
}

func TestRouter_MIMENormalization(t *testing.T) {
	r := NewRouter(Config{})
	// Trailing whitespace + params + uppercase all normalized away.
	got, err := r.Extract(context.Background(), "  TEXT/PLAIN  ; charset=UTF-8 ", "hint", []byte("ok"))
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got != "ok" {
		t.Fatalf("got %q, want ok", got)
	}
}
