// Package metrics writes InfluxDB line-protocol points to VictoriaMetrics /
// InfluxDB v3 Cloud. The endpoint shape mirrors pool-pump-planner/influx.go
// (POST /api/v2/write, Authorization: Token …) so both Go services hit the
// same surface.
package metrics

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"sort"
	"strconv"
	"strings"
	"time"
)

type Point struct {
	Measurement string
	Tags        map[string]string
	Fields      map[string]any
	Time        time.Time
}

func NewPoint(m string) *Point {
	return &Point{
		Measurement: m,
		Tags:        map[string]string{},
		Fields:      map[string]any{},
	}
}

func (p *Point) Tag(k, v string) *Point        { p.Tags[k] = v; return p }
func (p *Point) Field(k string, v any) *Point  { p.Fields[k] = v; return p }
func (p *Point) At(t time.Time) *Point         { p.Time = t; return p }

// LineProtocol serialises a point to a single line of Influx line protocol
// with second precision. Exported so tests can golden-file compare.
func (p *Point) LineProtocol() string {
	var b strings.Builder
	b.WriteString(escapeMeasurement(p.Measurement))

	tagKeys := sortedKeys(p.Tags)
	for _, k := range tagKeys {
		b.WriteString(",")
		b.WriteString(escapeTag(k))
		b.WriteString("=")
		b.WriteString(escapeTag(p.Tags[k]))
	}
	b.WriteString(" ")

	first := true
	for _, k := range sortedAnyKeys(p.Fields) {
		if !first {
			b.WriteString(",")
		}
		first = false
		b.WriteString(escapeTag(k))
		b.WriteString("=")
		b.WriteString(encodeField(p.Fields[k]))
	}
	if !p.Time.IsZero() {
		b.WriteString(" ")
		b.WriteString(strconv.FormatInt(p.Time.Unix(), 10))
	}
	return b.String()
}

func encodeField(v any) string {
	switch x := v.(type) {
	case int:
		return strconv.Itoa(x) + "i"
	case int64:
		return strconv.FormatInt(x, 10) + "i"
	case float64:
		return strconv.FormatFloat(x, 'f', -1, 64)
	case bool:
		if x {
			return "true"
		}
		return "false"
	case string:
		return `"` + strings.ReplaceAll(x, `"`, `\"`) + `"`
	default:
		return `"` + fmt.Sprintf("%v", x) + `"`
	}
}

func sortedKeys(m map[string]string) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}

func sortedAnyKeys(m map[string]any) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}

func escapeMeasurement(s string) string {
	s = strings.ReplaceAll(s, ",", `\,`)
	s = strings.ReplaceAll(s, " ", `\ `)
	return s
}

func escapeTag(s string) string {
	s = strings.ReplaceAll(s, ",", `\,`)
	s = strings.ReplaceAll(s, " ", `\ `)
	s = strings.ReplaceAll(s, "=", `\=`)
	return s
}

// Writer posts batches of points to Influx.
type Writer interface {
	Write(ctx context.Context, points []*Point) error
}

type HTTPWriter struct {
	Host     string
	Token    string
	Database string
	Client   *http.Client
}

func NewHTTP(host, token, database string) *HTTPWriter {
	return &HTTPWriter{
		Host:     host,
		Token:    token,
		Database: database,
		Client:   &http.Client{Timeout: 30 * time.Second},
	}
}

func (w *HTTPWriter) Write(ctx context.Context, points []*Point) error {
	if len(points) == 0 {
		return nil
	}
	host := w.Host
	if !strings.Contains(host, "://") {
		host = "https://" + host
	}
	host = strings.TrimRight(host, "/")

	q := url.Values{}
	q.Set("bucket", w.Database)
	q.Set("precision", "s")
	endpoint := host + "/api/v2/write?" + q.Encode()

	var body bytes.Buffer
	for _, p := range points {
		body.WriteString(p.LineProtocol())
		body.WriteString("\n")
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, &body)
	if err != nil {
		return err
	}
	req.Header.Set("Authorization", "Token "+w.Token)
	req.Header.Set("Content-Type", "text/plain; charset=utf-8")

	resp, err := w.Client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 300 {
		respBody, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("influx write %d: %s", resp.StatusCode, string(respBody))
	}
	return nil
}
