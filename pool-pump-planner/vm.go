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
	return c.queryPromInstantAt(promql, time.Time{}, lookbackDelta)
}

func (c *Config) queryPromInstantAt(promql string, at time.Time, lookbackDelta string) ([]promResult, error) {
	base := c.vmBaseURL()
	if base == "" {
		return nil, fmt.Errorf("VictoriaMetrics query URL not configured")
	}
	q := url.Values{}
	q.Set("query", promql)
	if !at.IsZero() {
		q.Set("time", strconv.FormatInt(at.Unix(), 10))
	}
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

// fetchWaterTempAt looks back up to 12h for the most recent pool temperature
// reading. The sensor publishes only on change (often hourly), so VM's default
// ~5m lookback regularly misses a valid-enough reading and drops the planner
// into fallback mode.
func (c *Config) fetchWaterTempAt(at time.Time) (float64, bool) {
	result, err := c.queryPromInstantAt("pool_temperature_value", at, "12h")
	if err != nil {
		log.Printf("[planner] water temp query failed: %v", err)
		return 0, false
	}
	if len(result) == 0 || len(result[0].Values) == 0 {
		return 0, false
	}
	return result[0].Values[0].Value, true
}

func (c *Config) fetchWaterTemp() (float64, bool) {
	return c.fetchWaterTempAt(time.Time{})
}

// deletePlanForDate removes any existing live-plan points tagged with the
// given plan_date, making the planner idempotent on re-run. Best-effort:
// logs and returns without error so a blocked or unavailable admin endpoint
// doesn't prevent the fresh write. DryRun short-circuits to no-op.
func (c *Config) deletePlanForDate(planDate string) {
	if c.DryRun {
		log.Printf("[planner] DRY RUN: skipping delete for plan_date=%s", planDate)
		return
	}
	base := c.vmBaseURL()
	if base == "" {
		log.Printf("[planner] VM URL not configured, skipping delete for plan_date=%s", planDate)
		return
	}
	q := url.Values{}
	q.Set("match[]", fmt.Sprintf(`{__name__=~"pool_iqpump_plan.*",run="live",plan_date="%s"}`, planDate))
	u := base + "/api/v1/admin/tsdb/delete_series?" + q.Encode()
	req, err := http.NewRequest("POST", u, nil)
	if err != nil {
		log.Printf("[planner] plan_date delete request build failed: %v, continuing", err)
		return
	}
	if c.VMToken != "" {
		req.Header.Set("Authorization", "Bearer "+c.VMToken)
	}
	client := &http.Client{Timeout: 30 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		log.Printf("[planner] plan_date delete failed: %v, continuing", err)
		return
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	if resp.StatusCode >= 400 {
		log.Printf("[planner] plan_date delete HTTP %d: %s, continuing", resp.StatusCode, string(body))
		return
	}
	log.Printf("[planner] deleted prior plan for plan_date=%s", planDate)
}
