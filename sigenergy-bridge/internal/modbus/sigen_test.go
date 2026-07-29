package modbus

import (
	"context"
	"io"
	"log/slog"
	"testing"
)

func TestU32FromRegs(t *testing.T) {
	cases := []struct {
		hi, lo uint16
		want   uint32
	}{
		{0x0000, 0x0000, 0},
		{0x0000, 0x0001, 1},
		{0x0001, 0x0000, 0x10000},
		{0xDEAD, 0xBEEF, 0xDEADBEEF},
	}
	for _, c := range cases {
		if got := U32FromRegs(c.hi, c.lo); got != c.want {
			t.Errorf("U32FromRegs(%x,%x) = %x want %x", c.hi, c.lo, got, c.want)
		}
	}
}

func TestEMSModeLabel(t *testing.T) {
	cases := map[int]string{
		0: "max_self_consumption",
		1: "sigen_ai",
		2: "tou",
		7: "remote_ems",
		9: "unknown(9)",
	}
	for in, want := range cases {
		if got := emsModeLabel(in); got != want {
			t.Errorf("emsModeLabel(%d) = %q want %q", in, got, want)
		}
	}
}

func TestDryRun_AllowsWrites(t *testing.T) {
	log := slog.New(slog.NewTextHandler(io.Discard, nil))
	dry := DryRun(&stubClient{}, log)

	ctx := context.Background()
	if err := dry.SetDischargeLimitW(ctx, 0); err != nil {
		t.Errorf("SetDischargeLimitW: %v", err)
	}
	if err := dry.SetChargingLimitW(ctx, 5000); err != nil {
		t.Errorf("SetChargingLimitW: %v", err)
	}
}

// stubClient is a no-op Client used to back DryRun in tests.
type stubClient struct{}

func (stubClient) Read(ctx context.Context) (*Readings, error) {
	return &Readings{}, nil
}
func (stubClient) ReadPlantMaxPowerW(ctx context.Context) (int, error)     { return 10000, nil }
func (stubClient) SetDischargeLimitW(ctx context.Context, watts int) error { return nil }
func (stubClient) SetChargingLimitW(ctx context.Context, watts int) error  { return nil }
func (stubClient) Close() error                                            { return nil }
