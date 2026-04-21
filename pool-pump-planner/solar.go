package main

import (
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"strconv"
	"strings"
	"time"
)

// fetchSolarForecast returns PV production (kWh) for each slot using
// forecast.solar's period watt-hours endpoint. On error, returns zeros.
func (c *Config) fetchSolarForecast(slots []time.Time) []float64 {
	out := make([]float64, len(slots))
	if c.GoogleLatLng == "" {
		log.Printf("[planner] GOOGLE_LAT_LNG not set, skipping solar forecast")
		return out
	}
	parts := strings.Split(c.GoogleLatLng, ",")
	if len(parts) != 2 {
		log.Printf("[planner] GOOGLE_LAT_LNG malformed: %q", c.GoogleLatLng)
		return out
	}
	lat := strings.TrimSpace(parts[0])
	lng := strings.TrimSpace(parts[1])
	u := fmt.Sprintf(
		"https://api.forecast.solar/estimate/watthours/period/%s/%s/%s/%s/%s",
		lat, lng,
		strconv.FormatFloat(c.PVDeclination, 'f', -1, 64),
		strconv.FormatFloat(c.PVAzimuth, 'f', -1, 64),
		strconv.FormatFloat(c.PVKWp, 'f', -1, 64),
	)

	client := &http.Client{Timeout: 20 * time.Second}
	resp, err := client.Get(u)
	if err != nil {
		log.Printf("[planner] forecast.solar fetch failed: %v", err)
		return out
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	if resp.StatusCode >= 400 {
		log.Printf("[planner] forecast.solar HTTP %d: %s", resp.StatusCode, string(body))
		return out
	}
	var parsed struct {
		Result map[string]any `json:"result"`
	}
	if err := json.Unmarshal(body, &parsed); err != nil {
		log.Printf("[planner] forecast.solar parse failed: %v", err)
		return out
	}
	// Keys are naive site-local timestamps "YYYY-MM-DD HH:MM:SS". Bucket by hour.
	byKey := make(map[string]float64)
	for k, v := range parsed.Result {
		t, err := time.ParseInLocation("2006-01-02 15:04:05", k, c.Timezone)
		if err != nil {
			continue
		}
		wh, ok := toFloat(v)
		if !ok {
			continue
		}
		key := t.Format("2006-01-02 15")
		byKey[key] += wh / 1000.0
	}
	slotsPerHour := float64(c.SlotsPerHour())
	for i, s := range slots {
		key := s.In(c.Timezone).Format("2006-01-02 15")
		out[i] = byKey[key] / slotsPerHour
	}
	return out
}

// fetchSolarHistoricalKWh returns PV production (kWh) per slot, derived from
// the inverter's historical power metric. Used in backfill mode where
// forecast.solar has no historical endpoint.
func (c *Config) fetchSolarHistoricalKWh(slots []time.Time) []float64 {
	out := make([]float64, len(slots))
	if len(slots) < 2 {
		return out
	}
	slotSeconds := slots[1].Unix() - slots[0].Unix()
	if slotSeconds <= 0 {
		slotSeconds = 900
	}
	slotRange := fmt.Sprintf("%ds", slotSeconds)
	promql := `avg_over_time(sigenergy_pv_power_power_kw{string="total"}[` + slotRange + `])`
	start := slots[0]
	end := slots[len(slots)-1]
	result, err := c.queryPromRange(promql, start, end, int(slotSeconds))
	if err != nil {
		log.Printf("[planner] historical solar query failed: %v", err)
		return out
	}
	if len(result) == 0 {
		return out
	}
	slotMinutes := int(slotSeconds / 60)
	return samplesToKWhPerSlot(result[0].Values, slots, slotMinutes)
}

// samplesToKWhPerSlot buckets kW samples onto slot start-times and converts
// to kWh assuming each sample represents the avg kW over one slot.
func samplesToKWhPerSlot(samples []promSample, slots []time.Time, slotMinutes int) []float64 {
	out := make([]float64, len(slots))
	slotHours := float64(slotMinutes) / 60.0
	byTs := make(map[int64]float64, len(samples))
	for _, s := range samples {
		byTs[int64(s.Timestamp)] = s.Value
	}
	for i, slot := range slots {
		if v, ok := byTs[slot.Unix()]; ok {
			out[i] = v * slotHours
		}
	}
	return out
}

func toFloat(v any) (float64, bool) {
	switch x := v.(type) {
	case float64:
		return x, true
	case int:
		return float64(x), true
	case int64:
		return float64(x), true
	case string:
		f, err := strconv.ParseFloat(x, 64)
		return f, err == nil
	default:
		return 0, false
	}
}
