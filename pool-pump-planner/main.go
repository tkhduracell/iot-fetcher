package main

import (
	"flag"
	"fmt"
	"log"
	"os"
	"strconv"
	"strings"
	"time"
)

func main() {
	log.SetFlags(log.LstdFlags | log.LUTC)

	if len(os.Args) >= 2 && os.Args[1] == "backfill" {
		cfg := loadConfig()
		if err := backfillCLI(cfg, os.Args[2:]); err != nil {
			log.Fatalf("backfill: %v", err)
		}
		return
	}

	once := flag.Bool("once", false, "run planner once and exit")
	dryRun := flag.Bool("dry-run", false, "fetch inputs and compute schedule, print everything, skip write to VictoriaMetrics")
	flag.Parse()

	cfg := loadConfig()
	cfg.DryRun = *dryRun

	if *once || *dryRun || (len(os.Args) > 1 && os.Args[1] == "once") {
		runPlanner(cfg)
		return
	}

	// Run once on startup (mirroring python schedule.run_all), then daily at POOL_PLAN_TIME.
	runPlanner(cfg)

	hh, mm, err := parseHHMM(cfg.PlanTime)
	if err != nil {
		log.Fatalf("invalid POOL_PLAN_TIME %q: %v", cfg.PlanTime, err)
	}

	for {
		next := nextDailyRun(time.Now().In(cfg.Timezone), hh, mm)
		delay := time.Until(next)
		log.Printf("[planner] next run at %s (%.0fs)", next.Format(time.RFC3339), delay.Seconds())
		time.Sleep(delay)
		runPlanner(cfg)
	}
}

// backfillCLI parses subcommand flags and dispatches to runBackfill.
func backfillCLI(cfg *Config, args []string) error {
	fs := flag.NewFlagSet("backfill", flag.ExitOnError)
	days := fs.Int("days", 30, "number of calendar days to backfill")
	endFlag := fs.String("end", "", "last day to plan (YYYY-MM-DD, site-local) — default: yesterday")
	dryRun := fs.Bool("dry-run", false, "compute + print the table, skip VM writes")
	if err := fs.Parse(args); err != nil {
		return err
	}

	var end time.Time
	if *endFlag == "" {
		now := time.Now().In(cfg.Timezone)
		end = time.Date(now.Year(), now.Month(), now.Day(), 0, 0, 0, 0, cfg.Timezone).AddDate(0, 0, -1)
	} else {
		d, err := time.ParseInLocation("2006-01-02", *endFlag, cfg.Timezone)
		if err != nil {
			return fmt.Errorf("--end %q: %w", *endFlag, err)
		}
		end = d
	}

	return runBackfill(cfg, *days, end, *dryRun)
}

func parseHHMM(s string) (int, int, error) {
	parts := strings.Split(s, ":")
	if len(parts) != 2 {
		return 0, 0, fmt.Errorf("expected HH:MM, got %q", s)
	}
	hh, err := strconv.Atoi(strings.TrimSpace(parts[0]))
	if err != nil {
		return 0, 0, fmt.Errorf("invalid hour %q: %w", parts[0], err)
	}
	mm, err := strconv.Atoi(strings.TrimSpace(parts[1]))
	if err != nil {
		return 0, 0, fmt.Errorf("invalid minute %q: %w", parts[1], err)
	}
	if hh < 0 || hh > 23 || mm < 0 || mm > 59 {
		return 0, 0, fmt.Errorf("out of range HH:MM in %q", s)
	}
	return hh, mm, nil
}

func nextDailyRun(now time.Time, hh, mm int) time.Time {
	candidate := time.Date(now.Year(), now.Month(), now.Day(), hh, mm, 0, 0, now.Location())
	if !candidate.After(now) {
		candidate = candidate.AddDate(0, 0, 1)
	}
	return candidate
}
