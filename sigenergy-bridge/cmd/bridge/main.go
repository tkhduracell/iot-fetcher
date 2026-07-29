package main

import (
	"context"
	"log/slog"
	"os"
	"os/signal"
	"strings"
	"syscall"

	"github.com/tkhduracell/iot-fetcher/sigenergy-bridge/internal/config"
	"github.com/tkhduracell/iot-fetcher/sigenergy-bridge/internal/controller"
	"github.com/tkhduracell/iot-fetcher/sigenergy-bridge/internal/ha"
	"github.com/tkhduracell/iot-fetcher/sigenergy-bridge/internal/metrics"
	"github.com/tkhduracell/iot-fetcher/sigenergy-bridge/internal/modbus"
)

func main() {
	log := newLogger()

	cfg, err := config.Load()
	if err != nil {
		log.Error("config load failed", "err", err)
		os.Exit(1)
	}

	log.Info("starting sigenergy-bridge",
		"sigenergy_host", cfg.SigenergyHost,
		"wallbox_entity", cfg.WallboxEntity,
		"dry_run", cfg.DryRun,
	)

	mb, err := modbus.NewTCP(modbus.Opts{
		Host: cfg.SigenergyHost,
		Port: cfg.SigenergyPort,
		Log:  log,
	})
	if err != nil {
		log.Error("modbus init failed", "err", err)
		os.Exit(1)
	}
	defer mb.Close()
	if cfg.DryRun {
		mb = modbus.DryRun(mb, log)
	}

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	listener := ha.Dial(ctx, cfg.HAURL, cfg.HAToken, cfg.WallboxEntity, log)
	writer := metrics.NewHTTP(cfg.InfluxHost, cfg.InfluxToken, cfg.InfluxDatabase)

	err = controller.Run(ctx, controller.Deps{
		Log:     log,
		Cfg:     cfg,
		Modbus:  mb,
		HA:      listener,
		Metrics: writer,
	})
	if err != nil && ctx.Err() == nil {
		log.Error("controller exited with error", "err", err)
		os.Exit(1)
	}
	log.Info("sigenergy-bridge stopped")
}

func newLogger() *slog.Logger {
	level := slog.LevelInfo
	switch strings.ToUpper(os.Getenv("LOG_LEVEL")) {
	case "DEBUG":
		level = slog.LevelDebug
	case "WARN":
		level = slog.LevelWarn
	case "ERROR":
		level = slog.LevelError
	}
	return slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: level}))
}
