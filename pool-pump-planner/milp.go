package main

import (
	"bufio"
	"bytes"
	"fmt"
	"math"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
)

type lpInput struct {
	costs       []float64
	blocked     map[int]bool
	minSlots    int
	targetSlots int
	maxSlots    int
	maxStarts   int
}

type lpResult struct {
	schedule []int   // 0/1 per slot
	slack    float64 // in slots
	status   string
}

// solveMILP writes a CBC LP file, invokes cbc, and reads the solution back.
// Mirrors the Python PuLP/CBC formulation in pool_pump_planner.py.
func solveMILP(in lpInput) (*lpResult, error) {
	dir, err := os.MkdirTemp("", "pool-pump-*")
	if err != nil {
		return nil, err
	}
	defer os.RemoveAll(dir)

	lpPath := filepath.Join(dir, "problem.lp")
	solPath := filepath.Join(dir, "solution.txt")
	if err := writeLP(lpPath, in); err != nil {
		return nil, err
	}

	cmd := exec.Command("cbc", lpPath, "solve", "solu", solPath)
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	if err := cmd.Run(); err != nil {
		return nil, fmt.Errorf("cbc failed: %w (stderr=%s)", err, stderr.String())
	}

	res, err := parseCBCSolution(solPath, len(in.costs))
	if err != nil {
		return nil, fmt.Errorf("parse cbc solution: %w (cbc stdout=%s)", err, stdout.String())
	}
	return res, nil
}

func writeLP(path string, in lpInput) error {
	T := len(in.costs)
	bigM := 100.0
	for _, c := range in.costs {
		if c > bigM {
			bigM = c
		}
	}
	bigM = bigM*10 + 100

	var b strings.Builder
	b.WriteString("\\* pool pump MILP *\\\n")
	b.WriteString("Minimize\n obj: ")
	first := true
	for t := 0; t < T; t++ {
		c := in.costs[t]
		if math.IsNaN(c) || c == 0 {
			continue
		}
		if !first {
			b.WriteString(" + ")
		}
		first = false
		fmt.Fprintf(&b, "%g x_%d", c, t)
	}
	if !first {
		b.WriteString(" + ")
	}
	fmt.Fprintf(&b, "%g slack\n", bigM)

	b.WriteString("Subject To\n")

	// min/target/max total run
	writeSum(&b, "c_min", T, "x", ">=", float64(in.minSlots))
	writeSumWithSlack(&b, "c_target", T, "x", "slack", ">=", float64(in.targetSlots))
	writeSum(&b, "c_max", T, "x", "<=", float64(in.maxSlots))

	// Start indicators: y_t >= x_t - x_{t-1}   (with x_{-1} = 0)
	for t := 0; t < T; t++ {
		if t == 0 {
			fmt.Fprintf(&b, " c_start_%d: y_%d - x_%d >= 0\n", t, t, t)
		} else {
			fmt.Fprintf(&b, " c_start_%d: y_%d - x_%d + x_%d >= 0\n", t, t, t, t-1)
		}
	}
	writeSum(&b, "c_maxstarts", T, "y", "<=", float64(in.maxStarts))

	// Blocked slots forced to 0
	for t := 0; t < T; t++ {
		if in.blocked[t] {
			fmt.Fprintf(&b, " c_blk_%d: x_%d = 0\n", t, t)
		}
	}

	b.WriteString("Bounds\n")
	b.WriteString(" slack >= 0\n")

	b.WriteString("Binary\n")
	for t := 0; t < T; t++ {
		fmt.Fprintf(&b, " x_%d", t)
		if t%10 == 9 {
			b.WriteString("\n")
		}
	}
	b.WriteString("\n")
	for t := 0; t < T; t++ {
		fmt.Fprintf(&b, " y_%d", t)
		if t%10 == 9 {
			b.WriteString("\n")
		}
	}
	b.WriteString("\nEnd\n")

	return os.WriteFile(path, []byte(b.String()), 0644)
}

func writeSum(b *strings.Builder, name string, T int, prefix, op string, rhs float64) {
	fmt.Fprintf(b, " %s:", name)
	for t := 0; t < T; t++ {
		if t == 0 {
			fmt.Fprintf(b, " %s_%d", prefix, t)
		} else {
			fmt.Fprintf(b, " + %s_%d", prefix, t)
		}
	}
	fmt.Fprintf(b, " %s %g\n", op, rhs)
}

func writeSumWithSlack(b *strings.Builder, name string, T int, prefix, slack, op string, rhs float64) {
	fmt.Fprintf(b, " %s:", name)
	for t := 0; t < T; t++ {
		if t == 0 {
			fmt.Fprintf(b, " %s_%d", prefix, t)
		} else {
			fmt.Fprintf(b, " + %s_%d", prefix, t)
		}
	}
	fmt.Fprintf(b, " + %s %s %g\n", slack, op, rhs)
}

// parseCBCSolution reads CBC's `solu` output. The first line holds the status
// (e.g. "Optimal - objective value 12.34"). Remaining lines hold per-variable
// assignments: `<index> <name> <value> <reducedCost>`.
func parseCBCSolution(path string, slots int) (*lpResult, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()

	res := &lpResult{schedule: make([]int, slots)}

	scanner := bufio.NewScanner(f)
	buf := make([]byte, 0, 1<<16)
	scanner.Buffer(buf, 1<<20)
	firstLine := true
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}
		if firstLine {
			firstLine = false
			res.status = line
			if !strings.HasPrefix(strings.ToLower(line), "optimal") {
				return nil, fmt.Errorf("non-optimal status: %s", line)
			}
			continue
		}
		fields := strings.Fields(line)
		if len(fields) < 3 {
			continue
		}
		name := fields[1]
		val, err := strconv.ParseFloat(fields[2], 64)
		if err != nil {
			continue
		}
		if strings.HasPrefix(name, "x_") {
			idx, err := strconv.Atoi(name[2:])
			if err != nil || idx < 0 || idx >= slots {
				continue
			}
			res.schedule[idx] = int(math.Round(val))
		} else if name == "slack" {
			res.slack = val
		}
	}
	if err := scanner.Err(); err != nil {
		return nil, err
	}
	return res, nil
}
