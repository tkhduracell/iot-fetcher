// Package controller wires the HA WebSocket listener, the Sigenergy Modbus
// client, and the VictoriaMetrics writer into a single state machine:
// clamp the battery's discharge to 0 while the Wallbox is charging, and
// hand control back to the inverter's default EMS when idle.
//
// Clamp strategy: enable remote EMS (40029=1) and set control mode to
// Standby (40031=0x01). In Standby the inverter neither charges nor
// discharges the ESS, so any PV flows straight to loads/grid and the grid
// covers the Wallbox without routing through the house battery. As a
// defence-in-depth we also write max-discharge=0 (40034). On exit we do
// the mirror: restore the discharge limit to the configured sentinel and
// disable remote EMS, which hands control back to the user's normal mode
// (typically max self-consumption or the Sigen AI mode).
package controller

import (
	"context"
	"fmt"
	"log/slog"
	"time"

	"github.com/tkhduracell/iot-fetcher/sigenergy-bridge/internal/config"
	"github.com/tkhduracell/iot-fetcher/sigenergy-bridge/internal/ha"
	"github.com/tkhduracell/iot-fetcher/sigenergy-bridge/internal/metrics"
	"github.com/tkhduracell/iot-fetcher/sigenergy-bridge/internal/modbus"
)

type Deps struct {
	Log     *slog.Logger
	Cfg     *config.Config
	Modbus  modbus.Client
	HA      ha.Listener
	Metrics metrics.Writer
	Now     func() time.Time
}

type state int

const (
	stateIdle state = iota
	stateClamped
)

func (s state) String() string {
	if s == stateClamped {
		return "CLAMPED"
	}
	return "IDLE"
}

// Run blocks until ctx is cancelled. On exit it attempts to restore
// unclamped discharge — callers should wait on it before exiting.
func Run(ctx context.Context, d Deps) error {
	if d.Now == nil {
		d.Now = time.Now
	}
	priorMode, err := d.Modbus.ReadOperatingMode(ctx)
	if err != nil {
		d.Log.WarnContext(ctx, "could not read operating mode on startup; assuming max self-consumption",
			"err", err)
		priorMode = modbus.EMSMaxSelfConsumption
	}

	// Resolve the "unlimited" discharge ceiling. Priority:
	//   1. Explicit SIGENERGY_DISCHARGE_UNLIMITED_W override (>0)
	//   2. Plant nameplate (register 30010) × (100 - margin)%
	//   3. Conservative 5 kW fallback if the Modbus read fails
	// Never write a value above the inverter's rated capability — firmware
	// should clamp, but "should" isn't "must", and a nameplate-matched
	// restore value is also a clearer record for audit.
	unlimitedW := d.Cfg.SigenergyUnlimitedW
	source := "env override"
	if unlimitedW <= 0 {
		nameplate, err := d.Modbus.ReadPlantMaxPowerW(ctx)
		if err != nil || nameplate <= 0 {
			d.Log.WarnContext(ctx, "plant nameplate read failed; using 5 kW conservative fallback",
				"err", err, "nameplate_w", nameplate)
			unlimitedW = 5000
			source = "fallback"
		} else {
			unlimitedW = nameplate * (100 - d.Cfg.DischargeMarginPct) / 100
			source = "auto-detect"
			d.Log.InfoContext(ctx, "auto-detected discharge ceiling from plant nameplate",
				"nameplate_w", nameplate, "margin_pct", d.Cfg.DischargeMarginPct, "unlimited_w", unlimitedW)
		}
	}

	d.Log.InfoContext(ctx, "controller starting",
		"prior_operating_mode", priorMode,
		"discharge_unlimited_w", unlimitedW,
		"discharge_unlimited_source", source,
		"poll_interval", d.Cfg.PollInterval,
		"failsafe_timeout", d.Cfg.HAFailsafeTimeout,
		"max_clamp_duration", d.Cfg.MaxClampDuration,
		"wallbox_entity", d.Cfg.WallboxEntity,
		"charging_states", d.Cfg.WallboxChargingStates,
	)
	// Freeze the resolved value into the config struct the rest of the
	// loop reads from, so clamp/unclamp use the same value consistently.
	d.Cfg.SigenergyUnlimitedW = unlimitedW

	cur := stateIdle
	var disconnectedSince time.Time
	var clampStartedAt time.Time

	pollTicker := time.NewTicker(d.Cfg.PollInterval)
	defer pollTicker.Stop()

	d.poll(ctx) // poll once immediately so metrics land without waiting.

	failsafeTick := time.NewTicker(d.Cfg.HAFailsafeTimeout / 3)
	defer failsafeTick.Stop()

	for {
		select {
		case <-ctx.Done():
			return d.shutdown(context.Background(), cur)

		case <-pollTicker.C:
			d.poll(ctx)

		case ev := <-d.HA.Events():
			charging := isCharging(ev.NewState, d.Cfg.WallboxChargingStates)
			d.Log.InfoContext(ctx, "ha event",
				"entity", ev.EntityID, "old", ev.OldState, "new", ev.NewState, "charging", charging)
			switch {
			case charging && cur == stateIdle:
				if err := d.clamp(ctx, "wallbox_charging"); err != nil {
					d.Log.ErrorContext(ctx, "clamp failed", "err", err)
					continue
				}
				cur = stateClamped
				clampStartedAt = d.Now()
			case !charging && cur == stateClamped:
				if err := d.unclamp(ctx, "wallbox_idle"); err != nil {
					d.Log.ErrorContext(ctx, "unclamp failed", "err", err)
					continue
				}
				cur = stateIdle
				clampStartedAt = time.Time{}
			}

		case up := <-d.HA.Connected():
			if up {
				disconnectedSince = time.Time{}
			} else {
				disconnectedSince = d.Now()
			}

		case <-failsafeTick.C:
			if cur != stateClamped {
				continue
			}
			// Absolute deadline: never hold longer than MaxClampDuration,
			// even if the Wallbox somehow reports charging forever.
			if d.Cfg.MaxClampDuration > 0 && !clampStartedAt.IsZero() &&
				d.Now().Sub(clampStartedAt) >= d.Cfg.MaxClampDuration {
				d.Log.WarnContext(ctx, "clamp exceeded max duration; releasing",
					"started_at", clampStartedAt, "max_duration", d.Cfg.MaxClampDuration)
				if err := d.unclamp(ctx, "max_duration"); err != nil {
					d.Log.ErrorContext(ctx, "max-duration unclamp failed", "err", err)
					continue
				}
				cur = stateIdle
				clampStartedAt = time.Time{}
				continue
			}
			// HA-lost fail-safe: if we can't see Wallbox state for longer
			// than the configured timeout, release — better to over-
			// discharge the battery than to strand it clamped in the dark.
			if !disconnectedSince.IsZero() &&
				d.Now().Sub(disconnectedSince) >= d.Cfg.HAFailsafeTimeout {
				d.Log.WarnContext(ctx, "ha disconnected past failsafe timeout; unclamping",
					"disconnected_since", disconnectedSince)
				if err := d.unclamp(ctx, "failsafe"); err != nil {
					d.Log.ErrorContext(ctx, "failsafe unclamp failed", "err", err)
					continue
				}
				cur = stateIdle
				disconnectedSince = time.Time{}
				clampStartedAt = time.Time{}
			}
		}
	}
}

func isCharging(state string, chargingStates []string) bool {
	for _, s := range chargingStates {
		if state == s {
			return true
		}
	}
	return false
}

func (d *Deps) clamp(ctx context.Context, reason string) error {
	d.Log.InfoContext(ctx, "clamping discharge", "reason", reason)
	if err := d.Modbus.EnableRemoteEMS(ctx, modbus.ControlModeStandby); err != nil {
		return fmt.Errorf("enable remote EMS: %w", err)
	}
	if err := d.Modbus.SetDischargeLimitW(ctx, 0); err != nil {
		return fmt.Errorf("set discharge=0: %w", err)
	}
	d.emitControl(ctx, reason, 0, true)
	return nil
}

func (d *Deps) unclamp(ctx context.Context, reason string) error {
	d.Log.InfoContext(ctx, "restoring discharge", "reason", reason, "limit_w", d.Cfg.SigenergyUnlimitedW)
	if err := d.Modbus.SetDischargeLimitW(ctx, d.Cfg.SigenergyUnlimitedW); err != nil {
		return fmt.Errorf("restore discharge: %w", err)
	}
	if err := d.Modbus.DisableRemoteEMS(ctx); err != nil {
		return fmt.Errorf("disable remote EMS: %w", err)
	}
	d.emitControl(ctx, reason, d.Cfg.SigenergyUnlimitedW, false)
	return nil
}

func (d *Deps) shutdown(ctx context.Context, cur state) error {
	d.Log.InfoContext(ctx, "controller shutting down", "state", cur.String())
	if cur != stateClamped {
		return nil
	}
	shutCtx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	if err := d.unclamp(shutCtx, "shutdown"); err != nil {
		return err
	}
	return nil
}

func (d *Deps) emitControl(ctx context.Context, reason string, limitW int, active bool) {
	activeVal := 0
	if active {
		activeVal = 1
	}
	p := metrics.NewPoint("sigenergy_discharge_control").
		Tag("host", d.Cfg.SigenergyHost).
		Tag("reason", reason).
		Field("limit_w", limitW).
		Field("active", activeVal).
		At(d.Now())
	if err := d.Metrics.Write(ctx, []*metrics.Point{p}); err != nil {
		d.Log.WarnContext(ctx, "metrics write (control) failed", "err", err)
	}
}

func (d *Deps) poll(ctx context.Context) {
	start := d.Now()
	r, err := d.Modbus.Read(ctx)
	if err != nil {
		d.Log.WarnContext(ctx, "modbus read failed", "err", err)
		return
	}
	points := readingsToPoints(d.Cfg.SigenergyHost, r, start)
	if err := d.Metrics.Write(ctx, points); err != nil {
		d.Log.WarnContext(ctx, "metrics write (poll) failed", "err", err)
	}
	end := d.Now()
	meta := metrics.NewPoint("sigenergy_bridge").
		Tag("host", d.Cfg.SigenergyHost).
		Field("duration_ms", end.Sub(start).Milliseconds()).
		At(end)
	if err := d.Metrics.Write(ctx, []*metrics.Point{meta}); err != nil {
		d.Log.WarnContext(ctx, "metrics write (meta) failed", "err", err)
	}
}

func readingsToPoints(host string, r *modbus.Readings, ts time.Time) []*metrics.Point {
	points := []*metrics.Point{
		metrics.NewPoint("sigenergy_system_status").
			Tag("host", host).
			Tag("operating_mode", r.OperatingMode).
			Tag("model_type", r.ModelType).
			Tag("running_state", r.RunningState).
			Field("on_grid", boolInt(r.OnGrid)).
			Field("grid_sensor_connected", boolInt(r.GridSensorConnected)).
			At(ts),
		metrics.NewPoint("sigenergy_grid_power").
			Tag("host", host).
			Field("net_power_kw", r.GridToKW-r.GridFromKW).
			Field("power_to_grid_kw", r.GridToKW).
			Field("power_from_grid_kw", r.GridFromKW).
			At(ts),
		metrics.NewPoint("sigenergy_battery").
			Tag("host", host).
			Field("soc_percent", r.BatterySOCPct).
			Field("power_to_battery_kw", r.ToBatteryKW).
			Field("power_from_battery_kw", r.FromBatteryKW).
			Field("avail_max_discharge_w", r.AvailMaxDischargeW).
			At(ts),
		metrics.NewPoint("sigenergy_pv_power").
			Tag("host", host).
			Tag("string", "total").
			Field("power_kw", r.PVTotalKW).
			At(ts),
	}
	if r.EMSControlOK {
		points = append(points, metrics.NewPoint("sigenergy_ems_control").
			Tag("host", host).
			Field("remote_ems_enabled", boolInt(r.RemoteEMSEnabled)).
			Field("control_mode", r.RemoteEMSControlMode).
			Field("max_discharge_limit_w", r.ESSMaxDischargeLimitW).
			Field("max_charge_limit_w", r.ESSMaxChargeLimitW).
			At(ts))
	}
	for i, kw := range r.PVStringKW {
		if kw <= 0 {
			continue
		}
		points = append(points, metrics.NewPoint("sigenergy_pv_power").
			Tag("host", host).
			Tag("string", fmt.Sprintf("string_%d", i+1)).
			Field("power_kw", kw).
			At(ts))
	}
	return points
}

func boolInt(b bool) int {
	if b {
		return 1
	}
	return 0
}
