package main

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func fakeVM(t *testing.T, byMetric map[string][]string) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		q := r.URL.Query().Get("query")
		var rows []string
		for m, vals := range byMetric {
			if strings.Contains(q, m) {
				for i, v := range vals {
					rows = append(rows, fmt.Sprintf(`{"metric":{"i":"%d"},"value":[1700000000,"%s"]}`, i, v))
				}
			}
		}
		fmt.Fprintf(w, `{"status":"success","data":{"result":[%s]}}`, strings.Join(rows, ","))
	}))
}

func TestFetchWaterTempAtTakesMinimum(t *testing.T) {
	srv := fakeVM(t, map[string][]string{
		"pool_iqpump_motordata_temperature": {"26"},
		"aqua_temp_temp_incoming":           {"20.5"},
	})
	defer srv.Close()
	got, ok := (&Config{VMURL: srv.URL}).fetchWaterTempAt(time.Now())
	if !ok || got != 20.5 {
		t.Fatalf("got %v,%v want 20.5,true", got, ok)
	}
}

func TestFetchWaterTempAtOneSourceMissing(t *testing.T) {
	srv := fakeVM(t, map[string][]string{"pool_iqpump_motordata_temperature": {"26"}})
	defer srv.Close()
	got, ok := (&Config{VMURL: srv.URL}).fetchWaterTempAt(time.Now())
	if !ok || got != 26 {
		t.Fatalf("got %v,%v want 26,true", got, ok)
	}
}

func TestFetchWaterTempAtNoData(t *testing.T) {
	srv := fakeVM(t, nil)
	defer srv.Close()
	if _, ok := (&Config{VMURL: srv.URL}).fetchWaterTempAt(time.Now()); ok {
		t.Fatal("want ok=false with no data")
	}
}

func TestFetchWaterTempAtFiltersZeroInQuery(t *testing.T) {
	var queries []string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		queries = append(queries, r.URL.Query().Get("query"))
		fmt.Fprint(w, `{"status":"success","data":{"result":[]}}`)
	}))
	defer srv.Close()
	(&Config{VMURL: srv.URL}).fetchWaterTempAt(time.Now())
	for _, q := range queries {
		if !strings.HasSuffix(q, "> 0") {
			t.Errorf("query %q does not filter zeros", q)
		}
	}
	if len(queries) != len(waterTempMetrics) {
		t.Errorf("got %d queries, want %d", len(queries), len(waterTempMetrics))
	}
}
