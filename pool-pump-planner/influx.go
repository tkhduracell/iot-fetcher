package main

import (
	"bytes"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/tkhduracell/iot-fetcher/influxmetrics"
)

// WritePoints ships points via the InfluxDB v2 line protocol endpoint. This is
// the same surface VictoriaMetrics and InfluxDB v3 Cloud accept, mirroring the
// Python influx.write_influx helper.
func (c *Config) WritePoints(points []*metrics.Point) error {
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
		body.WriteString(p.LineProtocol())
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
