package modbus

import (
	"context"
	"fmt"
	"log/slog"
	"sync"
	"time"

	smb "github.com/simonvetter/modbus"
)

// Readings mirrors the fields today's Python sigenergy.py poll produces so
// the Go service can emit identical VictoriaMetrics series.
type Readings struct {
	OperatingMode       string
	ModelType           string
	OnGrid              bool
	GridSensorConnected bool
	GridToKW            float64 // export (positive half of signed grid power)
	GridFromKW          float64 // import (negative half)
	BatterySOCPct       float64
	ToBatteryKW         float64 // charging (negative ESS power)
	FromBatteryKW       float64 // discharging (positive ESS power)
	PVTotalKW           float64
	PVStringKW          [4]float64 // per-string, plant-wide aggregation — 0 when unknown

	// Extended plant block (30049..30051). Zero/empty when read fails.
	AvailMaxDischargeW int    // 30049, available max ESS discharge, watts
	RunningState       string // 30051, standby/running/fault/shutdown

	// Remote EMS holding registers (40029..40036). Valid only when EMSControlOK.
	EMSControlOK          bool
	RemoteEMSEnabled      bool
	RemoteEMSControlMode  int
	ESSMaxChargeLimitW    int // 40032
	ESSMaxDischargeLimitW int // 40034
}

// Client is the Sigenergy surface consumed by the controller.
type Client interface {
	Read(ctx context.Context) (*Readings, error)
	// ReadPlantMaxPowerW returns the plant nameplate (register 30010) in
	// watts. Used at startup to auto-size the "unlimited" discharge
	// sentinel so we never write a value above what the inverter is built
	// to handle.
	ReadPlantMaxPowerW(ctx context.Context) (int, error)
	SetDischargeLimitW(ctx context.Context, watts int) error
	SetChargingLimitW(ctx context.Context, watts int) error
	Close() error
}

type Opts struct {
	Host    string
	Port    int
	Timeout time.Duration
	Log     *slog.Logger
}

// NewTCP returns a Modbus TCP client configured for the given host. The
// underlying TCP connection is opened lazily on first I/O and retried on
// every subsequent I/O after a failure. This keeps the service resilient
// to inverter reboots or transient network blips — and means local dev
// can run against an unreachable inverter without the service crashing.
func NewTCP(opts Opts) (Client, error) {
	if opts.Timeout == 0 {
		opts.Timeout = 5 * time.Second
	}
	if opts.Log == nil {
		opts.Log = slog.Default()
	}
	url := fmt.Sprintf("tcp://%s:%d", opts.Host, opts.Port)
	client, err := smb.NewClient(&smb.ClientConfiguration{
		URL:     url,
		Timeout: opts.Timeout,
	})
	if err != nil {
		return nil, fmt.Errorf("new modbus client: %w", err)
	}
	return &tcpClient{opts: opts, client: client}, nil
}

type tcpClient struct {
	opts   Opts
	client *smb.ModbusClient
	mu     sync.Mutex // simonvetter is not goroutine-safe
	opened bool
}

func (c *tcpClient) Close() error {
	c.mu.Lock()
	defer c.mu.Unlock()
	if !c.opened {
		return nil
	}
	c.opened = false
	return c.client.Close()
}

// ensureOpen opens the TCP connection on demand. Caller must hold c.mu.
func (c *tcpClient) ensureOpen() error {
	if c.opened {
		return nil
	}
	if err := c.client.Open(); err != nil {
		return fmt.Errorf("open modbus: %w", err)
	}
	c.opened = true
	return nil
}

// withConnection runs fn while holding the Modbus TCP connection, then
// disconnects. The SigenStor only accepts a single concurrent client, so
// holding the connection persistently would lock out the mySigen app,
// HA integrations, and anything else trying to reach the inverter. This
// matches the retired Python module, which also connected-polled-closed
// per invocation.
func (c *tcpClient) withConnection(fn func() error) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	if err := c.ensureOpen(); err != nil {
		return err
	}
	defer func() {
		_ = c.client.Close()
		c.opened = false
	}()
	return fn()
}

// readInputLocked reads `count` input registers. Must be called with the
// connection already open (via withConnection).
func (c *tcpClient) readInputLocked(addr, count uint16, slave uint8) ([]uint16, error) {
	if err := c.client.SetUnitId(slave); err != nil {
		return nil, err
	}
	return c.client.ReadRegisters(addr, count, smb.INPUT_REGISTER)
}

// readHoldingLocked reads `count` holding registers. Must be called with the
// connection already open (via withConnection).
func (c *tcpClient) readHoldingLocked(addr, count uint16, slave uint8) ([]uint16, error) {
	if err := c.client.SetUnitId(slave); err != nil {
		return nil, err
	}
	return c.client.ReadRegisters(addr, count, smb.HOLDING_REGISTER)
}

// readInput is a convenience wrapper for single-call reads that don't
// need a shared connection with other I/O.
func (c *tcpClient) readInput(addr, count uint16, slave uint8) ([]uint16, error) {
	var out []uint16
	err := c.withConnection(func() error {
		var e error
		out, e = c.readInputLocked(addr, count, slave)
		return e
	})
	return out, err
}

func (c *tcpClient) writeHolding(addr uint16, values []uint16, slave uint8) error {
	return c.withConnection(func() error {
		if err := c.client.SetUnitId(slave); err != nil {
			return err
		}
		if len(values) == 1 {
			return c.client.WriteRegister(addr, values[0])
		}
		return c.client.WriteRegisters(addr, values)
	})
}

func (c *tcpClient) ReadPlantMaxPowerW(ctx context.Context) (int, error) {
	regs, err := c.readInput(RegPlantMaxActivePower, 2, SlaveIDPlant)
	if err != nil {
		return 0, fmt.Errorf("read plant max power: %w", err)
	}
	// U32 register pair, raw value = kW × 1000 = watts.
	return int(U32FromRegs(regs[0], regs[1])), nil
}

func (c *tcpClient) SetDischargeLimitW(ctx context.Context, watts int) error {
	return c.writeU32KW(ctx, RegPlantESSMaxDischargeLimitKW, watts, "discharge")
}

func (c *tcpClient) SetChargingLimitW(ctx context.Context, watts int) error {
	return c.writeU32KW(ctx, RegPlantESSMaxChargeLimitKW, watts, "charge")
}

// writeU32KW takes a power limit in watts and writes it to a U32 register
// that expects raw = kW × 1000 (i.e. watts × 1).
func (c *tcpClient) writeU32KW(ctx context.Context, addr uint16, watts int, label string) error {
	if watts < 0 {
		return fmt.Errorf("%s limit must be non-negative, got %d W", label, watts)
	}
	// Register gain is ×1000 with unit kW, so the raw U32 value is the
	// watt count itself.
	raw := uint32(watts)
	hi := uint16(raw >> 16)
	lo := uint16(raw & 0xFFFF)
	c.opts.Log.InfoContext(ctx, "setting power limit", "label", label, "watts", watts, "addr", addr)
	return c.writeHolding(addr, []uint16{hi, lo}, SlaveIDPlant)
}

// Read pulls every metric field that the retired Python sigenergy.py poll
// used to produce so the emitted VictoriaMetrics series stay continuous
// across the migration. All plant-level reads share a single Modbus TCP
// connection; inverter-level reads (model type + PV strings) also share
// that connection but are tolerant of failures — if slave 1 can't be
// reached we still emit the plant metrics. The connection is closed when
// Read returns so other Modbus clients can talk to the inverter.
func (c *tcpClient) Read(ctx context.Context) (*Readings, error) {
	rd := &Readings{}

	var r1, r2, r3, r4 []uint16
	err := c.withConnection(func() error {
		var e error
		// Plant block 1: 30003..30014 (12 regs: EMSWorkMode through SOC).
		r1, e = c.readInputLocked(30003, 12, SlaveIDPlant)
		if e != nil {
			return fmt.Errorf("read plant block 30003: %w", e)
		}
		// Plant block 2: 30035..30038 (PV power S32 + ESS power S32).
		r2, e = c.readInputLocked(30035, 4, SlaveIDPlant)
		if e != nil {
			return fmt.Errorf("read plant block 30035: %w", e)
		}
		// Plant block 3: 30049..30051 (avail discharge U32 + running state). Non-fatal.
		r3, _ = c.readInputLocked(RegPlantAvailMaxDischargeW, 3, SlaveIDPlant)
		// Holding registers: 40029..40037 (remote EMS enable/mode + ESS limits). Non-fatal.
		r4, _ = c.readHoldingLocked(RegPlantRemoteEMSEnable, 9, SlaveIDPlant)
		// Inverter reads inline: model-type string (15 regs) + PV1..PV4
		// voltage/current pairs. Errors here are non-fatal.
		rd.ModelType = c.readModelTypeLocked(ctx)
		c.readPVStringsLocked(ctx, rd)
		return nil
	})
	if err != nil {
		return nil, err
	}

	emsMode := int(r1[0]) // 30003, U16
	rd.OperatingMode = emsModeLabel(emsMode)

	gridStatus := int(r1[1] & 0x00FF) // 30004, U8
	rd.GridSensorConnected = gridStatus == 1

	// 30005..30006 S32 grid active power, ×1000 kW.
	// Per spec + reference integration: >0 = importing (buying from grid),
	// <0 = exporting (selling to grid).
	gridW := int32(U32FromRegs(r1[2], r1[3]))
	if gridW > 0 {
		rd.GridFromKW = float64(gridW) / scaleKW
	} else if gridW < 0 {
		rd.GridToKW = float64(-gridW) / scaleKW
	}

	onOff := r1[6] // 30009
	rd.OnGrid = onOff == 0

	// 30014 SOC (U16, ×10).
	rd.BatterySOCPct = float64(r1[11]) / scalePercent

	// 30035..30036 PV power S32 ×1000 kW. PV is generation — treat as 0 if
	// negative (shouldn't happen in steady state).
	pvW := int32(U32FromRegs(r2[0], r2[1]))
	if pvW > 0 {
		rd.PVTotalKW = float64(pvW) / scaleKW
	}

	// 30037..30038 ESS power S32 ×1000 kW.
	// Per reference integration: <0 = discharging (energy leaves battery),
	// >0 = charging (energy enters battery).
	essW := int32(U32FromRegs(r2[2], r2[3]))
	if essW > 0 {
		rd.ToBatteryKW = float64(essW) / scaleKW
	} else if essW < 0 {
		rd.FromBatteryKW = float64(-essW) / scaleKW
	}

	if len(r3) >= 3 {
		rd.AvailMaxDischargeW = int(U32FromRegs(r3[0], r3[1]))
		rd.RunningState = runningStateLabel(int(r3[2]))
	} else {
		rd.RunningState = "unknown"
	}

	// Layout of the 9-register holding block starting at 40029:
	//   [0] 40029 RemoteEMSEnable   U16
	//   [1] 40030 (reserved)
	//   [2] 40031 RemoteEMSControlMode U16
	//   [3..4] 40032..33 ESSMaxChargeLimitKW  U32 (raw = W)
	//   [5..6] 40034..35 ESSMaxDischargeLimitKW U32 (raw = W)
	//   [7..8] 40036..37 PVMaxPowerLimitKW U32 (unused here)
	if len(r4) >= 7 {
		rd.EMSControlOK = true
		rd.RemoteEMSEnabled = r4[0] == 1
		rd.RemoteEMSControlMode = int(r4[2])
		rd.ESSMaxChargeLimitW = int(U32FromRegs(r4[3], r4[4]))
		rd.ESSMaxDischargeLimitW = int(U32FromRegs(r4[5], r4[6]))
	}

	return rd, nil
}

// readModelTypeLocked reads 30500..30514 (15 × U16 = 30 ASCII bytes) from
// the inverter. Must be called with the connection already open.
// Returns "unknown" on error so the poll can continue and still emit
// system-status metrics.
func (c *tcpClient) readModelTypeLocked(ctx context.Context) string {
	regs, err := c.readInputLocked(RegInverterModelType, 15, SlaveIDDevice)
	if err != nil {
		c.opts.Log.WarnContext(ctx, "inverter model-type read failed; using 'unknown'", "err", err)
		return "unknown"
	}
	buf := make([]byte, 0, 30)
	for _, r := range regs {
		buf = append(buf, byte(r>>8), byte(r&0xFF))
	}
	for len(buf) > 0 && (buf[len(buf)-1] == 0 || buf[len(buf)-1] == ' ') {
		buf = buf[:len(buf)-1]
	}
	if len(buf) == 0 {
		return "unknown"
	}
	return string(buf)
}

// readPVStringsLocked reads PV1..PV4 voltage+current pairs (31027..31034)
// and computes per-string kW. Must be called with the connection open.
// Values above a sane cap (50 kW/string) are discarded — disconnected
// strings report 0xFFFF on both voltage and current, which would otherwise
// decode to junk. This mirrors the "< 50" guard in the SigenAPI Python
// wrapper.
func (c *tcpClient) readPVStringsLocked(ctx context.Context, rd *Readings) {
	regs, err := c.readInputLocked(RegInverterPV1Voltage, 8, SlaveIDDevice)
	if err != nil {
		c.opts.Log.WarnContext(ctx, "inverter PV-string read failed; per-string metrics skipped", "err", err)
		return
	}
	for i := 0; i < 4; i++ {
		v := float64(int16(regs[i*2])) / scaleVolt
		a := float64(int16(regs[i*2+1])) / scaleAmp
		kw := v * a / 1000.0
		if kw > 0 && kw < 50 {
			rd.PVStringKW[i] = kw
		}
	}
}

func runningStateLabel(s int) string {
	switch s {
	case 0:
		return "standby"
	case 1:
		return "running"
	case 2:
		return "fault"
	case 3:
		return "shutdown"
	default:
		return fmt.Sprintf("unknown(%d)", s)
	}
}

func emsModeLabel(m int) string {
	switch m {
	case EMSMaxSelfConsumption:
		return "max_self_consumption"
	case EMSSigenAIMode:
		return "sigen_ai"
	case EMSTimeOfUse:
		return "tou"
	case EMSRemoteEMS:
		return "remote_ems"
	default:
		return fmt.Sprintf("unknown(%d)", m)
	}
}

// DryRun wraps any Client so writes are logged but not executed. Selected
// in main.go when DRY_RUN=true.
func DryRun(inner Client, log *slog.Logger) Client {
	return &dryRunClient{inner: inner, log: log}
}

type dryRunClient struct {
	inner Client
	log   *slog.Logger
}

func (d *dryRunClient) Read(ctx context.Context) (*Readings, error) {
	return d.inner.Read(ctx)
}
func (d *dryRunClient) ReadPlantMaxPowerW(ctx context.Context) (int, error) {
	return d.inner.ReadPlantMaxPowerW(ctx)
}
func (d *dryRunClient) SetDischargeLimitW(ctx context.Context, watts int) error {
	d.log.InfoContext(ctx, "dry-run: SetDischargeLimitW", "watts", watts)
	return nil
}
func (d *dryRunClient) SetChargingLimitW(ctx context.Context, watts int) error {
	d.log.InfoContext(ctx, "dry-run: SetChargingLimitW", "watts", watts)
	return nil
}
func (d *dryRunClient) Close() error { return d.inner.Close() }

// U32FromRegs combines two 16-bit Modbus registers (big-endian) into a
// single 32-bit value. Exported for unit testing.
func U32FromRegs(hi, lo uint16) uint32 {
	return uint32(hi)<<16 | uint32(lo)
}
