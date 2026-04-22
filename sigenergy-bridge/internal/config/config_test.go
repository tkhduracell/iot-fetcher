package config

import (
	"net"
	"strings"
	"testing"
	"time"
)

func setEnv(t *testing.T, kv map[string]string) {
	t.Helper()
	for k, v := range kv {
		t.Setenv(k, v)
	}
}

func validEnv() map[string]string {
	return map[string]string{
		"SIGENERGY_HOST":     "192.168.1.50",
		"HA_URL":             "ws://localhost:8123/api/websocket",
		"HA_TOKEN":           "tok",
		"WALLBOX_HA_ENTITY":  "sensor.wallbox_status",
		"INFLUX_HOST":        "http://host.docker.internal:8427",
		"INFLUX_TOKEN":       "infltok",
		"INFLUX_DATABASE":    "irisgatan",
	}
}

func TestLoad_Defaults(t *testing.T) {
	setEnv(t, validEnv())

	c, err := Load()
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if c.SigenergyPort != 502 {
		t.Errorf("default port: got %d want 502", c.SigenergyPort)
	}
	if c.SigenergyUnlimitedW != 0 {
		t.Errorf("default unlimited: got %d want 0 (auto-detect)", c.SigenergyUnlimitedW)
	}
	if c.DischargeMarginPct != 10 {
		t.Errorf("default margin pct: got %d want 10", c.DischargeMarginPct)
	}
	if c.PollInterval != 60*time.Second {
		t.Errorf("default poll: got %v", c.PollInterval)
	}
	if c.HAFailsafeTimeout != 120*time.Second {
		t.Errorf("default failsafe: got %v", c.HAFailsafeTimeout)
	}
	if c.MaxClampDuration != 4*time.Hour {
		t.Errorf("default max clamp duration: got %v", c.MaxClampDuration)
	}
	if len(c.WallboxChargingStates) != 1 || c.WallboxChargingStates[0] != "Charging" {
		t.Errorf("default charging states: %v", c.WallboxChargingStates)
	}
	if c.DryRun {
		t.Error("DryRun default should be false")
	}
}

func TestLoad_RejectsUnreasonableMargin(t *testing.T) {
	env := validEnv()
	env["SIGENERGY_DISCHARGE_MARGIN_PCT"] = "60"
	setEnv(t, env)

	_, err := Load()
	if err == nil || !strings.Contains(err.Error(), "DISCHARGE_MARGIN_PCT") {
		t.Fatalf("expected margin-range error, got %v", err)
	}
}

func TestLoad_MissingRequired(t *testing.T) {
	env := validEnv()
	delete(env, "SIGENERGY_HOST")
	setEnv(t, env)

	_, err := Load()
	if err == nil || !strings.Contains(err.Error(), "SIGENERGY_HOST") {
		t.Fatalf("expected missing-SIGENERGY_HOST error, got %v", err)
	}
}

func TestLoad_NonPrivateIP(t *testing.T) {
	env := validEnv()
	env["SIGENERGY_HOST"] = "8.8.8.8"
	setEnv(t, env)

	_, err := Load()
	if err == nil || !strings.Contains(err.Error(), "RFC1918 or loopback") {
		t.Fatalf("expected private-address rejection, got %v", err)
	}
}

func TestLoad_ChargingStatesCSV(t *testing.T) {
	env := validEnv()
	env["WALLBOX_CHARGING_STATES"] = "Charging, Charging Paused ,Waiting"
	setEnv(t, env)

	c, err := Load()
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	want := []string{"Charging", "Charging Paused", "Waiting"}
	if len(c.WallboxChargingStates) != len(want) {
		t.Fatalf("got %v want %v", c.WallboxChargingStates, want)
	}
	for i, s := range want {
		if c.WallboxChargingStates[i] != s {
			t.Errorf("idx %d: got %q want %q", i, c.WallboxChargingStates[i], s)
		}
	}
}

func TestLoad_InvalidDuration(t *testing.T) {
	env := validEnv()
	env["POLL_INTERVAL"] = "nonsense"
	setEnv(t, env)

	_, err := Load()
	if err == nil || !strings.Contains(err.Error(), "POLL_INTERVAL") {
		t.Fatalf("expected POLL_INTERVAL parse error, got %v", err)
	}
}

func TestIsPrivateOrLoopback(t *testing.T) {
	cases := []struct {
		ip   string
		want bool
	}{
		{"10.0.0.1", true},
		{"172.16.1.1", true},
		{"172.31.255.255", true},
		{"172.32.0.1", false},
		{"192.168.1.1", true},
		{"127.0.0.1", true},
		{"8.8.8.8", false},
	}
	for _, c := range cases {
		got := isPrivateOrLoopback(parseIPMust(t, c.ip))
		if got != c.want {
			t.Errorf("%s: got %v want %v", c.ip, got, c.want)
		}
	}
}

func parseIPMust(t *testing.T, s string) net.IP {
	t.Helper()
	ip := net.ParseIP(s)
	if ip == nil {
		t.Fatalf("bad test IP %q", s)
	}
	return ip
}
