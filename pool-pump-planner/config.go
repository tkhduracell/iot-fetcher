package main

import (
	"fmt"
	"log"
	"os"
	"strconv"
	"strings"
	"time"
)

type Config struct {
	// InfluxDB / VictoriaMetrics
	InfluxHost     string
	InfluxToken    string
	InfluxDatabase string
	VMURL          string
	VMToken        string

	// Location / PV
	GoogleLatLng  string
	PVDeclination float64
	PVAzimuth     float64
	PVKWp         float64

	// Pump
	PumpKW float64

	// Swedish electricity costs. Transfer fee and energy tax are billed only
	// on grid-imported energy; VAT is applied on top of the whole bill
	// (including the energy tax, per Skatteverket's "skatt på skatt" rule).
	TransferFeeSEKPerKWh float64 // elöverföring, per-kWh variable transfer fee, excl VAT
	EnergyTaxSEKPerKWh   float64 // energiskatt, excl VAT
	VATFraction          float64 // moms, e.g. 0.25 for 25%

	// Scheduling
	MinHours     int
	TargetHours  int
	MaxHours     int
	MaxStarts    int
	BlockedHours []int

	FallbackNightHours     []int
	FallbackAfternoonHours []int

	// Temperature driven target override
	TargetTempC          float64
	HeatingRateCPerHour  float64

	PriceArea    string
	Timezone     *time.Location
	SlotMinutes  int

	// Scheduling
	PlanTime string // HH:MM, site-local

	// Runtime flags
	DryRun bool // print inputs/schedule, skip VictoriaMetrics writes

	// Backfill selects the historical data fetchers (historical solar via VM
	// instead of forecast.solar). Set only by the backfill subcommand.
	Backfill bool
}

func loadConfig() *Config {
	cfg := &Config{
		InfluxHost:     getenv("INFLUX_HOST", ""),
		InfluxToken:    getenv("INFLUX_TOKEN", ""),
		InfluxDatabase: getenv("INFLUX_DATABASE", "irisgatan"),
		VMURL:          getenv("INFLUXDB_V3_URL", ""),
		VMToken:        getenv("INFLUXDB_V3_ACCESS_TOKEN", ""),

		GoogleLatLng:  getenv("GOOGLE_LAT_LNG", ""),
		PVDeclination: getenvFloat("POOL_PV_DECLINATION", 30),
		PVAzimuth:     getenvFloat("POOL_PV_AZIMUTH", 0),
		PVKWp:         getenvFloat("POOL_PV_KWP", 3.0),

		PumpKW: getenvFloat("POOL_PUMP_KW", 4.0),

		// Defaults calibrated to user's March 2026 E.ON invoice (SE4/MMO Malmö).
		TransferFeeSEKPerKWh: getenvFloat("POOL_TRANSFER_FEE_SEK_PER_KWH", 0.2584),
		EnergyTaxSEKPerKWh:   getenvFloat("POOL_ENERGY_TAX_SEK_PER_KWH", 0.36),
		VATFraction:          getenvFloat("POOL_VAT_FRACTION", 0.25),

		MinHours:     getenvInt("POOL_MIN_HOURS", 4),
		TargetHours:  getenvInt("POOL_TARGET_HOURS", 6),
		MaxHours:     getenvInt("POOL_MAX_HOURS", 10),
		MaxStarts:    getenvInt("POOL_MAX_STARTS", 2),
		BlockedHours: getenvIntList("POOL_BLOCKED_HOURS", []int{7, 8, 17, 18, 19, 20}),

		FallbackNightHours:     getenvIntList("POOL_FALLBACK_NIGHT_HOURS", []int{1, 2, 3, 4}),
		FallbackAfternoonHours: getenvIntList("POOL_FALLBACK_AFTERNOON_HOURS", []int{12, 13, 14, 15}),

		TargetTempC:         getenvFloat("POOL_TARGET_TEMP_C", 29),
		HeatingRateCPerHour: getenvFloat("POOL_HEATING_RATE_C_PER_HOUR", 0),

		PriceArea:   getenv("POOL_PRICE_AREA", "SE4"),
		SlotMinutes: getenvInt("POOL_SLOT_MINUTES", 15),
		PlanTime:    getenv("POOL_PLAN_TIME", "14:05"),
	}

	tzName := getenv("POOL_TIMEZONE", "Europe/Stockholm")
	tz, err := time.LoadLocation(tzName)
	if err != nil {
		tz = time.UTC
	}
	cfg.Timezone = tz

	if cfg.VMToken == "" {
		cfg.VMToken = cfg.InfluxToken
	}

	if err := cfg.validate(); err != nil {
		log.Fatalf("invalid configuration: %v", err)
	}
	return cfg
}

func (c *Config) validate() error {
	// SlotMinutes must evenly tile a clock hour so SlotsPerHour() stays well-defined
	// and plan timestamps align with Nord Pool's hourly pricing buckets.
	if c.SlotMinutes <= 0 || 60%c.SlotMinutes != 0 {
		return fmt.Errorf("POOL_SLOT_MINUTES must be a positive divisor of 60, got %d", c.SlotMinutes)
	}
	if c.MinHours < 0 || c.MaxHours < c.MinHours {
		return fmt.Errorf("POOL_MIN_HOURS (%d) must be <= POOL_MAX_HOURS (%d)", c.MinHours, c.MaxHours)
	}
	return nil
}

func (c *Config) SlotsPerHour() int { return 60 / c.SlotMinutes }
func (c *Config) HorizonSlots() int { return 24 * c.SlotsPerHour() }
func (c *Config) SlotHours() float64 {
	return float64(c.SlotMinutes) / 60.0
}

func getenv(k, def string) string {
	if v, ok := os.LookupEnv(k); ok && v != "" {
		return v
	}
	return def
}

func getenvInt(k string, def int) int {
	if v, ok := os.LookupEnv(k); ok && v != "" {
		n, err := strconv.Atoi(strings.TrimSpace(v))
		if err == nil {
			return n
		}
	}
	return def
}

func getenvFloat(k string, def float64) float64 {
	if v, ok := os.LookupEnv(k); ok && v != "" {
		f, err := strconv.ParseFloat(strings.TrimSpace(v), 64)
		if err == nil {
			return f
		}
	}
	return def
}

func getenvIntList(k string, def []int) []int {
	v, ok := os.LookupEnv(k)
	if !ok || strings.TrimSpace(v) == "" {
		return def
	}
	out := []int{}
	for _, p := range strings.Split(v, ",") {
		p = strings.TrimSpace(p)
		if p == "" {
			continue
		}
		n, err := strconv.Atoi(p)
		if err != nil {
			continue
		}
		out = append(out, n)
	}
	if len(out) == 0 {
		return def
	}
	return out
}
