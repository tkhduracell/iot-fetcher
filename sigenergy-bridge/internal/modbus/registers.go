package modbus

// Sigenergy SigenStor Modbus register map.
//
// Source: Sigenergy Modbus Protocol V1.7, 2024-04-09.
//
// Addressing: plant-level commands target slave ID 247 (SlaveIDPlant).
// Individual inverters sit behind slave ID 1 (SlaveIDDevice). Section 5.1
// defines read-only input registers (function 0x04). Section 5.2 defines
// read/write holding registers (functions 0x03/0x06/0x10).
const (
	SlaveIDPlant  = 247
	SlaveIDDevice = 1
)

// Plant read-only (input) registers — Section 5.1. Scale factor is applied
// as a divisor at decode time: the raw Modbus value equals engineering-value
// × gain.
const (
	RegPlantEMSWorkMode          uint16 = 30003 // U16, 0=max self-consumption, 1=Sigen AI, 2=TOU, 7=remote EMS
	RegPlantGridSensorStatus     uint16 = 30004 // U8, 0=not connected, 1=connected
	RegPlantGridSensorActivePow  uint16 = 30005 // S32, ×1000, kW (signed; >0 buying from grid, <0 selling to grid)
	RegPlantOnOffGridStatus      uint16 = 30009 // U16, 0=on-grid, 1=off-grid auto, 2=off-grid manual
	RegPlantMaxActivePower       uint16 = 30010 // U32, ×1000, kW (nameplate)
	RegPlantESSsoc               uint16 = 30014 // U16, ×10, %
	RegPlantPVPower              uint16 = 30035 // S32, ×1000, kW
	RegPlantESSPower             uint16 = 30037 // S32, ×1000, kW (<0 discharging, >0 charging)
	RegPlantAvailMaxDischargeW   uint16 = 30049 // U32, ×1000, kW
	RegPlantRunningState         uint16 = 30051 // U16, Appendix 1: 0=standby, 1=running, 2=fault, 3=shutdown
)

// Hybrid inverter read-only (input) registers — Section 5.3.
// Addressed via SlaveIDDevice (defaults to 1 for single-inverter plants).
const (
	RegInverterModelType     uint16 = 30500 // STRING, 15 regs (30 bytes ASCII)
	// PV per-string voltage/current live at 31027..31034. Not 30627 — that
	// range returns "illegal data address" on at least one real inverter
	// firmware. The community HA integration and the SigenAPI Python
	// wrapper both use 31027 and up.
	RegInverterPV1Voltage    uint16 = 31027 // S16, ×10, V
	RegInverterPV1Current    uint16 = 31028 // S16, ×100, A
	// PV2..PV4 sit at consecutive pairs through 31034.
	RegInverterPVTotalPower  uint16 = 31035 // S32, ×1000, kW
)

// Plant parameter setting (holding) registers — Section 5.2.
const (
	RegPlantStartStop           uint16 = 40000 // U16, 0=Stop, 1=Start
	RegPlantRemoteEMSEnable     uint16 = 40029 // U16, 0=disabled, 1=enabled
	RegPlantRemoteEMSControlMode uint16 = 40031 // U16, Appendix 6
	RegPlantESSMaxChargeLimitKW  uint16 = 40032 // U32, ×1000, kW (active when control mode = 3 or 4)
	RegPlantESSMaxDischargeLimitKW uint16 = 40034 // U32, ×1000, kW (per spec note: "active when control mode = 3 or 4"; likely also 5/6)
	RegPlantPVMaxPowerLimitKW    uint16 = 40036 // U32, ×1000, kW (active when control mode = 3,4,5,6)
)

// EMS work mode values (register 30003). Per spec section 5.1 comments.
const (
	EMSMaxSelfConsumption int = 0
	EMSSigenAIMode        int = 1
	EMSTimeOfUse          int = 2
	EMSRemoteEMS          int = 7
)

// Remote EMS control modes (register 40031) — Appendix 6. Standby is the
// safe "don't touch the battery" choice we use while the Wallbox is
// charging: the inverter neither charges nor discharges the ESS, so any PV
// flows to loads/grid and grid covers the Wallbox without routing through
// the battery.
const (
	ControlModePCSRemote              int = 0x00
	ControlModeStandby                int = 0x01
	ControlModeMaxSelfConsumption     int = 0x02
	ControlModeCommandChargingGrid    int = 0x03
	ControlModeCommandChargingPV      int = 0x04
	ControlModeCommandDischargingPV   int = 0x05
	ControlModeCommandDischargingESS  int = 0x06
)

// Scale factors.
const (
	scaleKW      = 1000.0 // power registers: raw = kW × 1000
	scalePercent = 10.0   // SOC register: raw = % × 10
	scaleVolt    = 10.0   // inverter PV voltage: raw = V × 10
	scaleAmp     = 100.0  // inverter PV current: raw = A × 100
)
