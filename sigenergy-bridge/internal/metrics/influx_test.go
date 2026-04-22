package metrics

import (
	"bytes"
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"
)

func TestLineProtocol_Golden(t *testing.T) {
	fixed := time.Unix(1700000000, 0).UTC()

	points := []*Point{
		NewPoint("sigenergy_battery").
			Tag("host", "192.168.1.50").
			Field("soc_percent", 55.0).
			Field("power_to_battery_kw", 0.0).
			Field("power_from_battery_kw", 1.2).
			At(fixed),
		NewPoint("sigenergy_discharge_control").
			Tag("host", "192.168.1.50").
			Tag("reason", "wallbox_charging").
			Field("limit_w", 0).
			Field("active", 1).
			At(fixed),
		// Escape edge cases: comma in measurement, space in tag key/value,
		// equals in field key, string field with spaces and quotes.
		NewPoint("weird,measure").
			Tag("tag key", "tag value").
			Field("field=name", 3.14).
			Field("str field", `a "b" c`).
			At(fixed),
	}

	var got bytes.Buffer
	for _, p := range points {
		got.WriteString(p.LineProtocol())
		got.WriteString("\n")
	}

	// Allow regenerating the golden file with UPDATE_GOLDEN=1 go test ...
	if os.Getenv("UPDATE_GOLDEN") == "1" {
		if err := os.WriteFile("testdata/golden.txt", got.Bytes(), 0o644); err != nil {
			t.Fatalf("write golden: %v", err)
		}
	}

	want, err := os.ReadFile("testdata/golden.txt")
	if err != nil {
		t.Fatalf("read golden: %v", err)
	}
	// Normalise field-name golden encoding: encodeField actually outputs
	// "power_from_battery_kw=1.2" etc. Simpler to rebuild the expected
	// battery line here for clarity.
	wantStr := string(want)
	// For battery line, both fields must appear in alpha order.
	// We keep golden.txt authoritative; if this assertion fails, rerun
	// with UPDATE_GOLDEN=1 to regenerate (after reviewing the diff).
	if got.String() != wantStr {
		t.Errorf("line protocol mismatch:\n---got---\n%s\n---want---\n%s", got.String(), wantStr)
	}
}

func TestEncodeField(t *testing.T) {
	cases := []struct {
		in   any
		want string
	}{
		{42, "42i"},
		{int64(42), "42i"},
		{3.14, "3.14"},
		{true, "true"},
		{false, "false"},
		{`hi "there"`, `"hi \"there\""`},
	}
	for _, c := range cases {
		if got := encodeField(c.in); got != c.want {
			t.Errorf("encodeField(%v) = %q want %q", c.in, got, c.want)
		}
	}
}

func TestEscapes(t *testing.T) {
	if got := escapeMeasurement("a, b"); got != `a\,\ b` {
		t.Errorf("escapeMeasurement: %q", got)
	}
	if got := escapeTag("a=b c"); got != `a\=b\ c` {
		t.Errorf("escapeTag: %q", got)
	}
}

func TestHTTPWriter_Write(t *testing.T) {
	var gotBody string
	var gotAuth string
	var gotQuery string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotAuth = r.Header.Get("Authorization")
		gotQuery = r.URL.RawQuery
		body, _ := io.ReadAll(r.Body)
		gotBody = string(body)
		w.WriteHeader(http.StatusNoContent)
	}))
	defer srv.Close()

	w := NewHTTP(srv.URL, "tok", "mybucket")
	err := w.Write(context.Background(), []*Point{
		NewPoint("m").Tag("h", "x").Field("v", 1.0).At(time.Unix(100, 0)),
	})
	if err != nil {
		t.Fatalf("Write: %v", err)
	}
	if gotAuth != "Token tok" {
		t.Errorf("auth: %q", gotAuth)
	}
	if !strings.Contains(gotQuery, "bucket=mybucket") || !strings.Contains(gotQuery, "precision=s") {
		t.Errorf("query: %q", gotQuery)
	}
	if !strings.HasPrefix(gotBody, "m,h=x v=1 100") {
		t.Errorf("body: %q", gotBody)
	}
}

func TestHTTPWriter_WriteError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusBadRequest)
		_, _ = w.Write([]byte("nope"))
	}))
	defer srv.Close()

	w := NewHTTP(srv.URL, "tok", "b")
	err := w.Write(context.Background(), []*Point{NewPoint("m").Field("v", 1)})
	if err == nil || !strings.Contains(err.Error(), "400") {
		t.Errorf("expected 400 error, got %v", err)
	}
}

func TestHTTPWriter_EmptyPoints(t *testing.T) {
	w := NewHTTP("http://nowhere.invalid", "tok", "b")
	if err := w.Write(context.Background(), nil); err != nil {
		t.Errorf("empty batch should be a no-op, got %v", err)
	}
}
