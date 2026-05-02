package controller

import (
	"context"
	"io"
	"log/slog"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/tkhduracell/iot-fetcher/sigenergy-bridge/internal/config"
	"github.com/tkhduracell/iot-fetcher/sigenergy-bridge/internal/ha"
	"github.com/tkhduracell/iot-fetcher/sigenergy-bridge/internal/metrics"
	"github.com/tkhduracell/iot-fetcher/sigenergy-bridge/internal/modbus"
)

// --- fakes ---

type fakeModbus struct {
	mu        sync.Mutex
	calls     []string // ordered trace of write calls (type:arg)
	readCalls atomic.Int32
}

func (f *fakeModbus) Read(ctx context.Context) (*modbus.Readings, error) {
	f.readCalls.Add(1)
	return &modbus.Readings{
		OperatingMode: "sigen_ai",
		ModelType:     "SigenStor",
		BatterySOCPct: 55,
		FromBatteryKW: 1.2,
	}, nil
}
func (f *fakeModbus) ReadPlantMaxPowerW(ctx context.Context) (int, error) {
	return 10000, nil
}
func (f *fakeModbus) SetDischargeLimitW(ctx context.Context, watts int) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	if watts == 0 {
		f.calls = append(f.calls, "discharge=0")
	} else {
		f.calls = append(f.calls, "discharge=unlimited")
	}
	return nil
}
func (f *fakeModbus) SetChargingLimitW(ctx context.Context, watts int) error { return nil }
func (f *fakeModbus) Close() error                                           { return nil }

type fakeHA struct {
	events    chan ha.Event
	connected chan bool
}

func (f *fakeHA) Events() <-chan ha.Event { return f.events }
func (f *fakeHA) Connected() <-chan bool  { return f.connected }

type fakeMetrics struct {
	mu     sync.Mutex
	points [][]*metrics.Point
}

func (m *fakeMetrics) Write(ctx context.Context, p []*metrics.Point) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.points = append(m.points, p)
	return nil
}

func (m *fakeMetrics) controlReasons() []string {
	m.mu.Lock()
	defer m.mu.Unlock()
	var reasons []string
	for _, batch := range m.points {
		for _, p := range batch {
			if p.Measurement == "sigenergy_discharge_control" {
				reasons = append(reasons, p.Tags["reason"])
			}
		}
	}
	return reasons
}

func quietLog() *slog.Logger { return slog.New(slog.NewTextHandler(io.Discard, nil)) }

func newCfg() *config.Config {
	return &config.Config{
		SigenergyHost:         "192.168.1.50",
		SigenergyUnlimitedW:   50000,
		WallboxEntity:         "sensor.wallbox_status",
		WallboxChargingStates: []string{"Charging"},
		PollInterval:          time.Hour,
		HAFailsafeTimeout:     300 * time.Millisecond,
		MaxClampDuration:      4 * time.Hour,
	}
}

func newDeps(t *testing.T) (*fakeModbus, *fakeHA, *fakeMetrics, Deps) {
	fm := &fakeModbus{}
	fh := &fakeHA{events: make(chan ha.Event, 4), connected: make(chan bool, 4)}
	fmx := &fakeMetrics{}
	d := Deps{
		Log:     quietLog(),
		Cfg:     newCfg(),
		Modbus:  fm,
		HA:      fh,
		Metrics: fmx,
		Now:     time.Now,
	}
	return fm, fh, fmx, d
}

func TestRun_ChargingStartedThenStopped(t *testing.T) {
	fm, fh, fmx, d := newDeps(t)

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	done := make(chan error, 1)
	go func() { done <- Run(ctx, d) }()

	fh.events <- ha.Event{EntityID: "sensor.wallbox_status", OldState: "Ready", NewState: "Charging"}
	waitFor(t, func() bool { return callCount(fm) >= 1 })

	fh.events <- ha.Event{EntityID: "sensor.wallbox_status", OldState: "Charging", NewState: "Ready"}
	waitFor(t, func() bool { return callCount(fm) >= 2 })

	cancel()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("Run did not return after cancel")
	}

	fm.mu.Lock()
	defer fm.mu.Unlock()
	want := []string{"discharge=0", "discharge=unlimited"}
	if !equal(fm.calls, want) {
		t.Errorf("calls: got %v want %v", fm.calls, want)
	}
	reasons := fmx.controlReasons()
	if len(reasons) != 2 || reasons[0] != "wallbox_charging" || reasons[1] != "wallbox_idle" {
		t.Errorf("control reasons: %v", reasons)
	}
}

func TestRun_FailsafeOnHADisconnect(t *testing.T) {
	fm, fh, fmx, d := newDeps(t)
	d.Cfg.HAFailsafeTimeout = 60 * time.Millisecond

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	done := make(chan error, 1)
	go func() { done <- Run(ctx, d) }()

	fh.events <- ha.Event{EntityID: "sensor.wallbox_status", OldState: "Ready", NewState: "Charging"}
	waitFor(t, func() bool { return callCount(fm) >= 1 })

	fh.connected <- false

	waitFor(t, func() bool { return callCount(fm) >= 2 })

	cancel()
	<-done

	reasons := fmx.controlReasons()
	if len(reasons) < 2 || reasons[len(reasons)-1] != "failsafe" {
		t.Errorf("expected failsafe reason, got %v", reasons)
	}
}

func TestRun_ShutdownRestoresIfClamped(t *testing.T) {
	fm, fh, fmx, d := newDeps(t)

	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- Run(ctx, d) }()

	fh.events <- ha.Event{EntityID: "sensor.wallbox_status", OldState: "Ready", NewState: "Charging"}
	waitFor(t, func() bool { return callCount(fm) >= 1 })

	cancel()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("Run did not return after cancel")
	}

	fm.mu.Lock()
	defer fm.mu.Unlock()
	last := fm.calls[len(fm.calls)-1]
	if last != "discharge=unlimited" {
		t.Errorf("last call should restore discharge; got %v (%v)", last, fm.calls)
	}
	reasons := fmx.controlReasons()
	if len(reasons) < 2 || reasons[len(reasons)-1] != "shutdown" {
		t.Errorf("last reason should be shutdown, got %v", reasons)
	}
}

func TestRun_MaxClampDurationReleases(t *testing.T) {
	fm, fh, fmx, d := newDeps(t)
	// Short max-duration plus a tight failsafe ticker (via short HA failsafe
	// timeout) so the check fires quickly. MaxClampDuration is what gets
	// tripped first because HA stays connected.
	d.Cfg.HAFailsafeTimeout = 60 * time.Millisecond
	d.Cfg.MaxClampDuration = 80 * time.Millisecond

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	done := make(chan error, 1)
	go func() { done <- Run(ctx, d) }()

	fh.events <- ha.Event{EntityID: "sensor.wallbox_status", OldState: "Ready", NewState: "Charging"}
	waitFor(t, func() bool { return callCount(fm) >= 1 })

	// Do NOT disconnect HA — we want to verify max-duration (not failsafe).
	waitFor(t, func() bool { return callCount(fm) >= 2 })

	cancel()
	<-done

	reasons := fmx.controlReasons()
	if len(reasons) < 2 || reasons[len(reasons)-1] != "max_duration" {
		t.Errorf("expected max_duration reason, got %v", reasons)
	}
}

// TestRun_PollRunsWithoutHAConnection verifies the core operational
// requirement: VictoriaMetrics poll writes must continue regardless of
// whether the HA WebSocket ever connects or stays up. We drive a listener
// that never emits Events or Connected signals (simulating "HA unreachable
// for the whole session") and confirm the poll ticker still fires reads
// and metric writes.
func TestRun_PollRunsWithoutHAConnection(t *testing.T) {
	fm, _, fmx, d := newDeps(t)
	// Tight poll interval for the test; leave HA channels silent.
	d.Cfg.PollInterval = 30 * time.Millisecond

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	done := make(chan error, 1)
	go func() { done <- Run(ctx, d) }()

	// Wait for ≥3 Modbus reads — one eager initial poll + two more from
	// the ticker. If the controller serialised poll behind HA, this would
	// never reach 3.
	waitFor(t, func() bool { return fm.readCalls.Load() >= 3 })

	cancel()
	<-done

	// And at least three poll-metric batches should have been written.
	fmx.mu.Lock()
	defer fmx.mu.Unlock()
	batches := 0
	for _, batch := range fmx.points {
		for _, p := range batch {
			if p.Measurement != "sigenergy_discharge_control" {
				batches++
				break
			}
		}
	}
	if batches < 3 {
		t.Errorf("expected ≥3 poll metric batches, got %d", batches)
	}
}

func TestRun_AutoDetectsDischargeCeiling(t *testing.T) {
	fm, fh, _, d := newDeps(t)
	// Blank explicit override → auto-detect from the fakeModbus.
	// ReadPlantMaxPowerW() returns 10 kW; with default 10% margin we
	// expect the restore value to be 9 kW = 9000 W.
	d.Cfg.SigenergyUnlimitedW = 0
	d.Cfg.DischargeMarginPct = 10

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	done := make(chan error, 1)
	go func() { done <- Run(ctx, d) }()

	// Drive one clamp/unclamp so we see the discharge writes.
	fh.events <- ha.Event{EntityID: "sensor.wallbox_status", OldState: "Ready", NewState: "Charging"}
	waitFor(t, func() bool { return callCount(fm) >= 1 })
	fh.events <- ha.Event{EntityID: "sensor.wallbox_status", OldState: "Charging", NewState: "Ready"}
	waitFor(t, func() bool { return callCount(fm) >= 2 })

	cancel()
	<-done

	if d.Cfg.SigenergyUnlimitedW != 9000 {
		t.Errorf("expected auto-detected 9000 W (10 kW × 90%%), got %d", d.Cfg.SigenergyUnlimitedW)
	}
}

func TestIsCharging(t *testing.T) {
	states := []string{"Charging", "Charging Paused"}
	if !isCharging("Charging", states) {
		t.Error("Charging should match")
	}
	if !isCharging("Charging Paused", states) {
		t.Error("Charging Paused should match")
	}
	if isCharging("Ready", states) {
		t.Error("Ready should not match")
	}
}

func callCount(fm *fakeModbus) int {
	fm.mu.Lock()
	defer fm.mu.Unlock()
	return len(fm.calls)
}

func equal(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

func waitFor(t *testing.T, cond func() bool) {
	t.Helper()
	deadline := time.Now().Add(time.Second)
	for time.Now().Before(deadline) {
		if cond() {
			return
		}
		time.Sleep(5 * time.Millisecond)
	}
	t.Fatal("condition never became true")
}
