package config

import (
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"
)

type Config struct {
	// HealthInterval is how often container health is polled.
	HealthInterval time.Duration
	// UnhealthyGrace is how long a container must stay unhealthy before it is
	// restarted, so a single flapping check doesn't trigger a restart.
	UnhealthyGrace time.Duration
	// RestartMax caps restarts per container inside RestartWindow.
	RestartMax    int
	RestartWindow time.Duration
	// RestartAllUnhealthy restarts any container with a healthcheck. When
	// false, only containers labelled watchdog.restart=true are restarted.
	RestartAllUnhealthy bool

	UpdateInterval time.Duration
	// UpdateLabels: a container is auto-updated when any of these labels is
	// "true". wud.watch is kept so existing compose labels keep working.
	UpdateLabels []string
	Prune        bool

	// SelfName is this container's name; it is never recreated in place.
	SelfName  string
	StatePath string

	InfluxHost     string
	InfluxToken    string
	InfluxDatabase string

	DryRun bool
}

func Load() (*Config, error) {
	c := &Config{
		RestartMax:          getInt("RESTART_MAX", 3),
		RestartAllUnhealthy: getBool("RESTART_ALL_UNHEALTHY", true),
		UpdateLabels:        splitCSV(getenv("UPDATE_LABELS", "watchdog.update,wud.watch")),
		Prune:               getBool("PRUNE", true),
		SelfName:            getenv("SELF_NAME", "watchdog"),
		StatePath:           getenv("STATE_PATH", "/data/restarts.json"),
		InfluxHost:          os.Getenv("INFLUX_HOST"),
		InfluxToken:         os.Getenv("INFLUX_TOKEN"),
		InfluxDatabase:      getenv("INFLUX_DATABASE", "irisgatan"),
		DryRun:              getBool("DRY_RUN", false),
	}
	var err error
	if c.HealthInterval, err = getDuration("HEALTH_INTERVAL", 30*time.Second); err != nil {
		return nil, err
	}
	if c.UnhealthyGrace, err = getDuration("UNHEALTHY_GRACE", 2*time.Minute); err != nil {
		return nil, err
	}
	if c.RestartWindow, err = getDuration("RESTART_WINDOW", 24*time.Hour); err != nil {
		return nil, err
	}
	if c.UpdateInterval, err = getDuration("UPDATE_INTERVAL", 5*time.Minute); err != nil {
		return nil, err
	}
	for k, d := range map[string]time.Duration{
		"HEALTH_INTERVAL": c.HealthInterval, "UPDATE_INTERVAL": c.UpdateInterval, "RESTART_WINDOW": c.RestartWindow,
	} {
		if d <= 0 {
			return nil, fmt.Errorf("%s must be > 0, got %s", k, d)
		}
	}
	if c.RestartMax < 0 {
		return nil, fmt.Errorf("RESTART_MAX must be >= 0, got %d", c.RestartMax)
	}
	return c, nil
}

func getenv(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}

func getInt(k string, def int) int {
	if v, err := strconv.Atoi(os.Getenv(k)); err == nil {
		return v
	}
	return def
}

func getBool(k string, def bool) bool {
	v := os.Getenv(k)
	if v == "" {
		return def
	}
	return strings.EqualFold(v, "true") || v == "1"
}

func getDuration(k string, def time.Duration) (time.Duration, error) {
	v := os.Getenv(k)
	if v == "" {
		return def, nil
	}
	d, err := time.ParseDuration(v)
	if err != nil {
		return 0, fmt.Errorf("%s: %w", k, err)
	}
	return d, nil
}

func splitCSV(s string) []string {
	var out []string
	for _, p := range strings.Split(s, ",") {
		if p = strings.TrimSpace(p); p != "" {
			out = append(out, p)
		}
	}
	return out
}
