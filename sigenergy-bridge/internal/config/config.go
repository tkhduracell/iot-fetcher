package config

import (
	"errors"
	"fmt"
	"net"
	"os"
	"strconv"
	"strings"
	"time"
)

type Config struct {
	SigenergyHost            string
	SigenergyPort            int
	// SigenergyUnlimitedW, when > 0, overrides the auto-detected discharge
	// ceiling. Leave at 0 (default) to read the plant nameplate from
	// register 30010 at startup and apply DischargeMarginPct.
	SigenergyUnlimitedW      int
	// DischargeMarginPct is the safety percentage shaved off the
	// auto-detected nameplate (e.g. 10 → use 90% of nameplate as the
	// "unlimited" restore value). Ignored when SigenergyUnlimitedW is set.
	DischargeMarginPct       int
	HAURL                    string
	HAToken                  string
	WallboxEntity            string
	WallboxChargingStates    []string
	InfluxHost               string
	InfluxToken              string
	InfluxDatabase           string
	PollInterval             time.Duration
	HAFailsafeTimeout        time.Duration
	MaxClampDuration         time.Duration
	DryRun                   bool
}

func Load() (*Config, error) {
	c := &Config{
		SigenergyHost:       os.Getenv("SIGENERGY_HOST"),
		SigenergyPort:       getInt("SIGENERGY_PORT", 502),
		SigenergyUnlimitedW: getInt("SIGENERGY_DISCHARGE_UNLIMITED_W", 0), // 0 = auto-detect
		DischargeMarginPct:  getInt("SIGENERGY_DISCHARGE_MARGIN_PCT", 10),
		HAURL:               os.Getenv("HA_URL"),
		HAToken:             os.Getenv("HA_TOKEN"),
		WallboxEntity:       os.Getenv("WALLBOX_HA_ENTITY"),
		InfluxHost:          os.Getenv("INFLUX_HOST"),
		InfluxToken:         os.Getenv("INFLUX_TOKEN"),
		InfluxDatabase:      os.Getenv("INFLUX_DATABASE"),
		DryRun:              strings.EqualFold(os.Getenv("DRY_RUN"), "true"),
	}

	c.WallboxChargingStates = splitCSV(getenv("WALLBOX_CHARGING_STATES", "Charging"))
	var err error
	if c.PollInterval, err = getDuration("POLL_INTERVAL", 60*time.Second); err != nil {
		return nil, err
	}
	if c.HAFailsafeTimeout, err = getDuration("HA_FAILSAFE_TIMEOUT", 120*time.Second); err != nil {
		return nil, err
	}
	if c.MaxClampDuration, err = getDuration("MAX_CLAMP_DURATION", 4*time.Hour); err != nil {
		return nil, err
	}

	if err := c.validate(); err != nil {
		return nil, err
	}
	return c, nil
}

func (c *Config) validate() error {
	missing := []string{}
	if c.SigenergyHost == "" {
		missing = append(missing, "SIGENERGY_HOST")
	}
	if c.HAURL == "" {
		missing = append(missing, "HA_URL")
	}
	if c.HAToken == "" {
		missing = append(missing, "HA_TOKEN")
	}
	if c.WallboxEntity == "" {
		missing = append(missing, "WALLBOX_HA_ENTITY")
	}
	if c.InfluxHost == "" {
		missing = append(missing, "INFLUX_HOST")
	}
	if c.InfluxToken == "" {
		missing = append(missing, "INFLUX_TOKEN")
	}
	if c.InfluxDatabase == "" {
		missing = append(missing, "INFLUX_DATABASE")
	}
	if len(missing) > 0 {
		return fmt.Errorf("missing required env vars: %s", strings.Join(missing, ", "))
	}
	if len(c.WallboxChargingStates) == 0 {
		return errors.New("WALLBOX_CHARGING_STATES must contain at least one state")
	}
	if c.DischargeMarginPct < 0 || c.DischargeMarginPct > 50 {
		return fmt.Errorf("SIGENERGY_DISCHARGE_MARGIN_PCT must be in [0, 50], got %d", c.DischargeMarginPct)
	}
	// SIGENERGY_HOST must be RFC1918 — prevents a misconfigured staging token
	// from accidentally writing Modbus commands to the prod inverter.
	ip := net.ParseIP(c.SigenergyHost)
	if ip == nil {
		return fmt.Errorf("SIGENERGY_HOST must be a valid IP address, got %q", c.SigenergyHost)
	}
	if !isPrivateOrLoopback(ip) {
		return fmt.Errorf("SIGENERGY_HOST must be RFC1918 or loopback, got %s", ip)
	}
	return nil
}

// isPrivateOrLoopback accepts RFC1918 ranges plus loopback. Loopback is
// allowed for SSH-tunnelled local dev; writes through a tunnel still only
// reach a caller-controlled destination.
func isPrivateOrLoopback(ip net.IP) bool {
	v4 := ip.To4()
	if v4 == nil {
		return false
	}
	switch {
	case v4[0] == 10:
		return true
	case v4[0] == 127:
		return true
	case v4[0] == 172 && v4[1] >= 16 && v4[1] <= 31:
		return true
	case v4[0] == 192 && v4[1] == 168:
		return true
	}
	return false
}

func getenv(k, def string) string {
	if v, ok := os.LookupEnv(k); ok && v != "" {
		return v
	}
	return def
}

func getInt(k string, def int) int {
	if v, ok := os.LookupEnv(k); ok && v != "" {
		if n, err := strconv.Atoi(strings.TrimSpace(v)); err == nil {
			return n
		}
	}
	return def
}

func getDuration(k string, def time.Duration) (time.Duration, error) {
	v, ok := os.LookupEnv(k)
	if !ok || strings.TrimSpace(v) == "" {
		return def, nil
	}
	d, err := time.ParseDuration(strings.TrimSpace(v))
	if err != nil {
		return 0, fmt.Errorf("%s: %w", k, err)
	}
	return d, nil
}

func splitCSV(s string) []string {
	parts := strings.Split(s, ",")
	out := make([]string, 0, len(parts))
	for _, p := range parts {
		p = strings.TrimSpace(p)
		if p != "" {
			out = append(out, p)
		}
	}
	return out
}
