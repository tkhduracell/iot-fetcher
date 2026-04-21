package main

import (
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func TestDeleteSeries_Success(t *testing.T) {
	start := time.Date(2026, 4, 21, 14, 0, 0, 0, time.UTC)
	var called atomic.Int32

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		called.Add(1)
		if r.Method != "POST" {
			t.Errorf("method = %s, want POST", r.Method)
		}
		if r.URL.Path != "/api/v1/admin/tsdb/delete_series" {
			t.Errorf("path = %s", r.URL.Path)
		}
		if got := r.Header.Get("Authorization"); got != "Bearer test-token" {
			t.Errorf("auth = %q", got)
		}
		if err := r.ParseForm(); err != nil {
			t.Fatalf("parse form: %v", err)
		}
		if matches := r.PostForm["match[]"]; len(matches) != 1 ||
			matches[0] != `{__name__=~"pool_iqpump_plan.*",run="live"}` {
			t.Errorf("match[] = %v", matches)
		}
		if got, want := r.PostForm.Get("start"), strconv.FormatInt(start.Unix(), 10); got != want {
			t.Errorf("start = %q, want %q", got, want)
		}
		w.WriteHeader(http.StatusNoContent)
	}))
	defer srv.Close()

	cfg := &Config{VMURL: srv.URL, VMToken: "test-token"}
	if err := cfg.deleteSeries(`{__name__=~"pool_iqpump_plan.*",run="live"}`, start); err != nil {
		t.Fatalf("deleteSeries: %v", err)
	}
	if called.Load() != 1 {
		t.Errorf("handler called %d times, want 1", called.Load())
	}
}

func TestDeleteSeries_ErrorStatus(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
		_, _ = w.Write([]byte("perm denied"))
	}))
	defer srv.Close()

	cfg := &Config{VMURL: srv.URL, VMToken: "tok"}
	err := cfg.deleteSeries(`{run="live"}`, time.Time{})
	if err == nil {
		t.Fatal("expected error, got nil")
	}
	if !strings.Contains(err.Error(), "500") || !strings.Contains(err.Error(), "perm denied") {
		t.Errorf("error = %v, want mention of 500 + body", err)
	}
}

func TestDeleteSeries_OmitsStartWhenZero(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = r.ParseForm()
		if r.PostForm.Has("start") {
			t.Errorf("start should be omitted for zero time, got %q", r.PostForm.Get("start"))
		}
		w.WriteHeader(http.StatusNoContent)
	}))
	defer srv.Close()

	cfg := &Config{VMURL: srv.URL, VMToken: "tok"}
	if err := cfg.deleteSeries(`{run="live"}`, time.Time{}); err != nil {
		t.Fatal(err)
	}
}

// writePlan fixture setup: a single mock server handles both the delete and
// write endpoints. Counts per-endpoint so tests can assert whether delete was
// called for a given run tag.
type writePlanMock struct {
	server      *httptest.Server
	deleteCalls atomic.Int32
	writeCalls  atomic.Int32
	deleteStart atomic.Int64
}

func newWritePlanMock(t *testing.T) *writePlanMock {
	t.Helper()
	m := &writePlanMock{}
	m.server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/v1/admin/tsdb/delete_series":
			m.deleteCalls.Add(1)
			if err := r.ParseForm(); err == nil {
				if s := r.PostForm.Get("start"); s != "" {
					if n, err := strconv.ParseInt(s, 10, 64); err == nil {
						m.deleteStart.Store(n)
					}
				}
			}
			w.WriteHeader(http.StatusNoContent)
		case "/api/v2/write":
			m.writeCalls.Add(1)
			w.WriteHeader(http.StatusNoContent)
		default:
			t.Errorf("unexpected request path: %s", r.URL.Path)
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	return m
}

func (m *writePlanMock) close() { m.server.Close() }

func newWritePlanCfg(url string) *Config {
	tz, _ := time.LoadLocation("Europe/Stockholm")
	return &Config{
		VMURL:          url,
		VMToken:        "tok",
		InfluxHost:     url,
		InfluxToken:    "tok",
		InfluxDatabase: "test",
		SlotMinutes:    30,
		PumpKW:         4.0,
		Timezone:       tz,
	}
}

func writePlanFixtureSlots() []time.Time {
	base := time.Date(2026, 4, 21, 14, 0, 0, 0, time.UTC)
	return []time.Time{base, base.Add(30 * time.Minute)}
}

func TestWritePlan_LiveCallsDelete(t *testing.T) {
	m := newWritePlanMock(t)
	defer m.close()
	cfg := newWritePlanCfg(m.server.URL)

	slots := writePlanFixtureSlots()
	sch := []int{1, 0}
	prices := []float64{0.5, 0.5}
	solar := []float64{0, 0}
	stats := planStats{plannedHours: 0.5, expectedCostSEK: 1.0, slackHours: 0, costPerSlot: []float64{1.0, 0}}

	err := writePlan(cfg, slots, sch, prices, solar, stats, 20.0, true, 6, "optimal", "none",
		map[string]string{"run": "live"})
	if err != nil {
		t.Fatalf("writePlan: %v", err)
	}
	if got := m.deleteCalls.Load(); got != 1 {
		t.Errorf("delete calls = %d, want 1", got)
	}
	if got, want := m.deleteStart.Load(), slots[0].Unix(); got != want {
		t.Errorf("delete start = %d, want %d", got, want)
	}
	if got := m.writeCalls.Load(); got != 1 {
		t.Errorf("write calls = %d, want 1", got)
	}
}

func TestWritePlan_NonLiveSkipsDelete(t *testing.T) {
	for _, runTag := range []string{"backfill", "baseline_night", "baseline_afternoon"} {
		t.Run(runTag, func(t *testing.T) {
			m := newWritePlanMock(t)
			defer m.close()
			cfg := newWritePlanCfg(m.server.URL)

			slots := writePlanFixtureSlots()
			sch := []int{1, 0}
			prices := []float64{0.5, 0.5}
			solar := []float64{0, 0}
			stats := planStats{plannedHours: 0.5, expectedCostSEK: 1.0, costPerSlot: []float64{1.0, 0}}

			err := writePlan(cfg, slots, sch, prices, solar, stats, 20.0, true, 6, "optimal", "none",
				map[string]string{"run": runTag, "anchor_date": "2026-04-20"})
			if err != nil {
				t.Fatalf("writePlan: %v", err)
			}
			if got := m.deleteCalls.Load(); got != 0 {
				t.Errorf("delete calls = %d for run=%q, want 0", got, runTag)
			}
			if got := m.writeCalls.Load(); got != 1 {
				t.Errorf("write calls = %d, want 1", got)
			}
		})
	}
}
