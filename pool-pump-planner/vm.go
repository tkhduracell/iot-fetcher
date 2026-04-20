package main

import (
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"
)

type promResponse struct {
	Status    string `json:"status"`
	ErrorType string `json:"errorType"`
	Error     string `json:"error"`
	Data      struct {
		Result []struct {
			Metric map[string]string `json:"metric"`
			Value  []any             `json:"value"`
			Values [][]any           `json:"values"`
		} `json:"result"`
	} `json:"data"`
}

func (c *Config) vmBaseURL() string {
	if c.VMURL != "" {
		u := strings.TrimRight(c.VMURL, "/")
		if !strings.Contains(u, "://") {
			u = "https://" + u
		}
		return u
	}
	if c.InfluxHost != "" {
		if strings.Contains(c.InfluxHost, "://") {
			return strings.TrimRight(c.InfluxHost, "/")
		}
		return "https://" + strings.TrimRight(c.InfluxHost, "/")
	}
	return ""
}

func (c *Config) queryPromInstant(promql, lookbackDelta string) ([]promResult, error) {
	base := c.vmBaseURL()
	if base == "" {
		return nil, fmt.Errorf("VictoriaMetrics query URL not configured")
	}
	q := url.Values{}
	q.Set("query", promql)
	if lookbackDelta != "" {
		q.Set("lookback_delta", lookbackDelta)
	}
	return c.promGet(base+"/api/v1/query?"+q.Encode(), false)
}

func (c *Config) queryPromRange(promql string, start, end time.Time, stepSeconds int) ([]promResult, error) {
	base := c.vmBaseURL()
	if base == "" {
		return nil, fmt.Errorf("VictoriaMetrics query URL not configured")
	}
	q := url.Values{}
	q.Set("query", promql)
	q.Set("start", strconv.FormatInt(start.Unix(), 10))
	q.Set("end", strconv.FormatInt(end.Unix(), 10))
	q.Set("step", strconv.Itoa(stepSeconds))
	return c.promGet(base+"/api/v1/query_range?"+q.Encode(), true)
}

type promResult struct {
	Metric map[string]string
	// For instant: single (ts, value). For range: many.
	Values []promSample
}

type promSample struct {
	Timestamp float64
	Value     float64
}

func (c *Config) promGet(u string, rangeQuery bool) ([]promResult, error) {
	req, err := http.NewRequest("GET", u, nil)
	if err != nil {
		return nil, err
	}
	if c.VMToken != "" {
		req.Header.Set("Authorization", "Bearer "+c.VMToken)
	}
	client := &http.Client{Timeout: 30 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	if resp.StatusCode >= 400 {
		return nil, fmt.Errorf("vm query %d: %s", resp.StatusCode, string(body))
	}
	var pr promResponse
	if err := json.Unmarshal(body, &pr); err != nil {
		return nil, fmt.Errorf("parse vm response: %w", err)
	}
	// Prometheus-compatible APIs return 200 with status=error for query-side
	// failures (bad PromQL, unknown metric, etc.) — surface those as errors
	// instead of masking them as empty results.
	if pr.Status != "" && pr.Status != "success" {
		return nil, fmt.Errorf("vm query error (%s): %s", pr.ErrorType, pr.Error)
	}
	out := make([]promResult, 0, len(pr.Data.Result))
	for _, r := range pr.Data.Result {
		pr := promResult{Metric: r.Metric}
		if rangeQuery {
			for _, v := range r.Values {
				if s, ok := parseSample(v); ok {
					pr.Values = append(pr.Values, s)
				}
			}
		} else if len(r.Value) == 2 {
			if s, ok := parseSample(r.Value); ok {
				pr.Values = append(pr.Values, s)
			}
		}
		out = append(out, pr)
	}
	return out, nil
}

func parseSample(v []any) (promSample, bool) {
	if len(v) != 2 {
		return promSample{}, false
	}
	var ts float64
	switch t := v[0].(type) {
	case float64:
		ts = t
	case int64:
		ts = float64(t)
	default:
		return promSample{}, false
	}
	var val float64
	switch x := v[1].(type) {
	case string:
		f, err := strconv.ParseFloat(x, 64)
		if err != nil {
			return promSample{}, false
		}
		val = f
	case float64:
		val = x
	default:
		return promSample{}, false
	}
	return promSample{Timestamp: ts, Value: val}, true
}

// fetchHourlyPrices returns one price per slot; slot prices inside the same
// clock hour share the same hourly price. Returned slice has the same length
// and order as slots; missing entries are NaN.
func (c *Config) fetchHourlyPrices(slots []time.Time) []float64 {
	out := make([]float64, len(slots))
	for i := range out {
		out[i] = nan()
	}
	if len(slots) == 0 {
		return out
	}
	promql := fmt.Sprintf(`energy_price_SEK_per_kWh{area="%s"}`, c.PriceArea)
	start := slots[0]
	end := slots[len(slots)-1]
	result, err := c.queryPromRange(promql, start, end, 3600)
	if err != nil {
		log.Printf("[planner] price query failed: %v", err)
		return out
	}
	if len(result) == 0 {
		result, err = c.queryPromRange(fmt.Sprintf(`energy_price{area="%s"}`, c.PriceArea), start, end, 3600)
		if err != nil {
			log.Printf("[planner] fallback price query failed: %v", err)
			return out
		}
	}
	if len(result) == 0 {
		return out
	}
	byHour := make(map[int64]float64)
	for _, s := range result[0].Values {
		bucket := (int64(s.Timestamp) / 3600) * 3600
		byHour[bucket] = s.Value
	}
	for i, s := range slots {
		bucket := (s.Unix() / 3600) * 3600
		if v, ok := byHour[bucket]; ok {
			out[i] = v
		}
	}
	return out
}

func (c *Config) fetchWaterTemp() (float64, bool) {
	result, err := c.queryPromInstant("pool_temperatur_value", "")
	if err != nil {
		log.Printf("[planner] water temp query failed: %v", err)
		return 0, false
	}
	if len(result) == 0 || len(result[0].Values) == 0 {
		return 0, false
	}
	return result[0].Values[0].Value, true
}
