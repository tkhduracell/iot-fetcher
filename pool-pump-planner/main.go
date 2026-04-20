package main

import (
	"flag"
	"log"
	"os"
	"strings"
	"time"
)

func main() {
	log.SetFlags(log.LstdFlags | log.LUTC)

	once := flag.Bool("once", false, "run planner once and exit")
	flag.Parse()

	cfg := loadConfig()

	if *once || (len(os.Args) > 1 && os.Args[1] == "once") {
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

func parseHHMM(s string) (int, int, error) {
	parts := strings.SplitN(s, ":", 2)
	if len(parts) != 2 {
		return 0, 0, errInvalidTime
	}
	hh, err1 := atoi(parts[0])
	mm, err2 := atoi(parts[1])
	if err1 != nil || err2 != nil || hh < 0 || hh > 23 || mm < 0 || mm > 59 {
		return 0, 0, errInvalidTime
	}
	return hh, mm, nil
}

var errInvalidTime = &timeParseErr{}

type timeParseErr struct{}

func (e *timeParseErr) Error() string { return "expected HH:MM" }

func atoi(s string) (int, error) {
	n := 0
	for _, c := range strings.TrimSpace(s) {
		if c < '0' || c > '9' {
			return 0, errInvalidTime
		}
		n = n*10 + int(c-'0')
	}
	return n, nil
}

func nextDailyRun(now time.Time, hh, mm int) time.Time {
	candidate := time.Date(now.Year(), now.Month(), now.Day(), hh, mm, 0, 0, now.Location())
	if !candidate.After(now) {
		candidate = candidate.AddDate(0, 0, 1)
	}
	return candidate
}
