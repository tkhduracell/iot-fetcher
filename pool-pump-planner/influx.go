package main

import (
	"bytes"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"
)

type Point struct {
	Measurement string
	Tags        map[string]string
	Fields      map[string]any // float64, int, bool, or string
	Time        time.Time
}

func NewPoint(m string) *Point {
	return &Point{
		Measurement: m,
		Tags:        map[string]string{},
		Fields:      map[string]any{},
	}
}

func (p *Point) Tag(k, v string) *Point      { p.Tags[k] = v; return p }
func (p *Point) Field(k string, v any) *Point { p.Fields[k] = v; return p }
func (p *Point) At(t time.Time) *Point        { p.Time = t; return p }

// Encode a single point in InfluxDB line protocol with second precision.
func (p *Point) lineProtocol() string {
	var b strings.Builder
	b.WriteString(escapeMeasurement(p.Measurement))
	// tags in sorted order for determinism
	keys := sortedKeys(p.Tags)
	for _, k := range keys {
		b.WriteString(",")
		b.WriteString(escapeTag(k))
		b.WriteString("=")
		b.WriteString(escapeTag(p.Tags[k]))
	}
	b.WriteString(" ")
	firstField := true
	for _, k := range sortedAnyKeys(p.Fields) {
		if !firstField {
			b.WriteString(",")
		}
		firstField = false
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
	sortStrings(keys)
	return keys
}

func sortedAnyKeys(m map[string]any) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sortStrings(keys)
	return keys
}

func sortStrings(s []string) {
	// small n, bubble-sort keeps us dependency-free
	for i := 1; i < len(s); i++ {
		for j := i; j > 0 && s[j-1] > s[j]; j-- {
			s[j-1], s[j] = s[j], s[j-1]
		}
	}
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

// WritePoints ships points via the InfluxDB v2 line protocol endpoint. This is
// the same surface VictoriaMetrics and InfluxDB v3 Cloud accept, mirroring the
// Python influx.write_influx helper.
func (c *Config) WritePoints(points []*Point) error {
	if c.InfluxHost == "" || c.InfluxToken == "" {
		log.Printf("[influx] INFLUX_HOST and INFLUX_TOKEN not set, skipping write of %d points", len(points))
		return nil
	}
	host := c.InfluxHost
	if !strings.Contains(host, "://") {
		host = "https://" + host
	}
	host = strings.TrimRight(host, "/")

	q := url.Values{}
	q.Set("bucket", c.InfluxDatabase)
	q.Set("precision", "s")
	endpoint := host + "/api/v2/write?" + q.Encode()

	var body bytes.Buffer
	for _, p := range points {
		body.WriteString(p.lineProtocol())
		body.WriteString("\n")
	}

	req, err := http.NewRequest("POST", endpoint, &body)
	if err != nil {
		return err
	}
	req.Header.Set("Authorization", "Token "+c.InfluxToken)
	req.Header.Set("Content-Type", "text/plain; charset=utf-8")

	client := &http.Client{Timeout: 30 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	respBody, _ := io.ReadAll(resp.Body)
	if resp.StatusCode >= 300 {
		return fmt.Errorf("influx write %d: %s", resp.StatusCode, string(respBody))
	}
	if len(points) > 4 {
		log.Printf("[influx] wrote %d points (%s ...)", len(points), points[0].Measurement)
	} else {
		names := make([]string, 0, len(points))
		for _, p := range points {
			names = append(names, p.Measurement)
		}
		log.Printf("[influx] wrote %d points (%s)", len(points), strings.Join(names, ", "))
	}
	return nil
}
