package dockerx

import (
	"testing"

	"github.com/moby/moby/api/types/container"
	ocispec "github.com/opencontainers/image-spec/specs-go/v1"
)

func TestHealthOf(t *testing.T) {
	for _, tc := range []struct {
		s    container.Summary
		want string
	}{
		{container.Summary{Status: "Up 19 hours (unhealthy)"}, "unhealthy"},
		{container.Summary{Status: "Up 2 minutes (healthy)"}, "healthy"},
		{container.Summary{Status: "Up 5 seconds (health: starting)"}, "starting"},
		{container.Summary{Status: "Up 2 days"}, ""},
		{container.Summary{Status: "Up 1 hour (unhealthy)", Health: &container.HealthSummary{Status: container.Healthy}}, "healthy"},
		{container.Summary{Health: &container.HealthSummary{Status: container.NoHealthcheck}}, ""},
	} {
		if got := healthOf(tc.s); got != tc.want {
			t.Errorf("healthOf(%q) = %q, want %q", tc.s.Status, got, tc.want)
		}
	}
}

func TestStripImageDefaults(t *testing.T) {
	img := &ocispec.ImageConfig{
		Cmd:        []string{"python", "main.py"},
		Entrypoint: []string{"/entry"},
		Env:        []string{"PATH=/usr/bin", "VERSION=1"},
		WorkingDir: "/app",
		Labels:     map[string]string{"org.version": "1"},
	}
	c := &container.Config{
		Cmd:        []string{"python", "main.py"}, // image default → dropped
		Entrypoint: []string{"/custom"},           // user override → kept
		Env:        []string{"PATH=/usr/bin", "VERSION=1", "TZ=Europe/Stockholm"},
		WorkingDir: "/app",
		Labels:     map[string]string{"org.version": "1", "wud.watch": "true"},
	}
	StripImageDefaults(c, img)
	if c.Cmd != nil || c.WorkingDir != "" {
		t.Errorf("image defaults kept: cmd=%v wd=%q", c.Cmd, c.WorkingDir)
	}
	if len(c.Entrypoint) != 1 || c.Entrypoint[0] != "/custom" {
		t.Errorf("user entrypoint lost: %v", c.Entrypoint)
	}
	if len(c.Env) != 1 || c.Env[0] != "TZ=Europe/Stockholm" {
		t.Errorf("env = %v, want only the user-set TZ", c.Env)
	}
	if _, ok := c.Labels["org.version"]; ok || c.Labels["wud.watch"] != "true" {
		t.Errorf("labels = %v", c.Labels)
	}
}
