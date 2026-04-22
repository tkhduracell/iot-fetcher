//go:build integration

package modbus

// Integration tests against a real Sigenergy SigenStor. Requires
// SIGENERGY_HOST env var pointing to a reachable inverter. Run via
//
//	go test -tags=integration ./internal/modbus
//
// These tests are intentionally empty until the register map is verified.
