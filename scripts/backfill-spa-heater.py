#!/usr/bin/env python3
"""Backfill spa_heater_on from the exporter's complementary hvac_action series.

The HA->VM exporter writes climate.bp2100g0's hvac_action as `value=0` when it is
"off", but as `state_text="heating"` (a string, stored as 0 by VM) when it is not.
The two series are therefore complementary, and the PRESENCE of a state_text sample
means the heater was running. Validated against HA's own recorder: presence-derived
duty over 10 days is 14.358% vs HA's 14.3%.

Writes `spa_heater_on,run=backfill value=0|1` at 1-minute resolution. Live samples
(from the HA template binary sensor) carry no `run` label, so the two never collide
-- but they must not overlap in time either, hence --end-before.

Dry-run by default; pass --write to actually POST.
"""
import argparse
import subprocess
import sys
import json
import os
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VM_QUERY = os.path.join(REPO_ROOT, "scripts", "vm-query.sh")
STATE_TEXT = "spa_climate_hvac_action_state_text"  # present <=> hvac_action != "off"
VALUE = "spa_climate_hvac_action_value"            # present <=> hvac_action == "off"


def _parse_ts(ts):
    """RFC3339 or unix-epoch string -> epoch seconds."""
    try:
        return float(ts)
    except ValueError:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


def _parse_step(step):
    mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}[step[-1]]
    return float(step[:-1]) * mult


# VictoriaMetrics rejects a query_range call whose point count exceeds
# -search.maxPointsPerTimeseries (default 30000). A months-long window at
# 60s resolution needs ~200k+ points, so vm_range chunks the request rather
# than sending it in one shot. MAX_POINTS is kept well under the 30000 cap
# for margin (each chunk is inclusive of both endpoints).
MAX_POINTS = 25000


def _vm_range_once(promql, start, end, step):
    out = subprocess.run(
        [VM_QUERY, "--format", "json", "range", promql,
         "--start", start, "--end", end, "--step", step],
        capture_output=True, text=True, timeout=300,
    )
    if out.returncode != 0:
        raise SystemExit(f"vm-query failed: {out.stderr[:2000]}")
    d = json.loads(out.stdout)
    if d.get("status") != "success":
        raise SystemExit(f"query error: {json.dumps(d)[:2000]}")
    r = d["data"]["result"]
    return {int(float(ts)): float(v) for ts, v in r[0]["values"]} if r else {}


def vm_range(promql, start, end, step="60s"):
    step_s = _parse_step(step)
    start_s, end_s = _parse_ts(start), _parse_ts(end)
    chunk_s = MAX_POINTS * step_s

    merged = {}
    chunk_start = start_s
    while chunk_start < end_s:
        chunk_end = min(chunk_start + chunk_s, end_s)
        merged.update(_vm_range_once(promql, str(int(chunk_start)), str(int(chunk_end)), step))
        chunk_start = chunk_end
    return merged


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True, help="RFC3339, e.g. 2026-03-01T00:00:00Z")
    ap.add_argument("--end", required=True, help="RFC3339; must precede the first live sample")
    ap.add_argument("--write", action="store_true", help="actually POST to VictoriaMetrics")
    args = ap.parse_args()

    # Both series are queried because count_over_time yields NO POINT (not 0) when
    # a window contains no samples. Reading only state_text would therefore return
    # exclusively "on" minutes and compute a 100% duty cycle.
    #
    #   state_text present -> hvac_action was heating/idle -> 1
    #   value present      -> hvac_action was "off"        -> 0
    #   neither present    -> exporter was down; emit nothing rather than
    #                         fabricating "off" across an outage.
    on_win = vm_range(f"count_over_time({STATE_TEXT}[2m])", args.start, args.end)
    off_win = vm_range(f"count_over_time({VALUE}[2m])", args.start, args.end)
    if not on_win:
        raise SystemExit("no state_text samples in range -- nothing to backfill")

    lines = []
    on_minutes = 0
    covered = sorted(set(on_win) | set(off_win))
    for ts in covered:
        # A 2m window straddling a transition can contain both; treat as on.
        on = 1 if on_win.get(ts, 0) > 0 else 0
        on_minutes += on
        lines.append(f"spa_heater_on,run=backfill value={on} {ts}")

    total = len(lines)
    gaps = 0
    if covered:
        expected = (covered[-1] - covered[0]) // 60 + 1
        gaps = max(0, expected - total)
    first, last = covered[0], covered[-1]
    print(f"range        : {datetime.fromtimestamp(first, tz=timezone.utc).isoformat()}"
          f" .. {datetime.fromtimestamp(last, tz=timezone.utc).isoformat()}")
    print(f"samples      : {total}")
    print(f"uncovered    : {gaps} min (exporter gaps -- no sample written)")
    print(f"heater on    : {on_minutes} min ({on_minutes/60:.1f} h)")
    print(f"duty         : {100*on_minutes/total:.3f}%  (expect ~14-18%)")
    print(f"last sample  : {datetime.fromtimestamp(last, tz=timezone.utc).isoformat()}"
          f"  <-- must precede the first live spa_heater_on sample")

    if not args.write:
        print("\nDRY RUN -- nothing written. Re-run with --write to POST.")
        return

    import urllib.request
    base = os.environ.get("VM_BASE_URL")
    token = os.environ.get("INFLUX_TOKEN")
    if not base or not token:
        raise SystemExit("set VM_BASE_URL and INFLUX_TOKEN to write "
                         "(source them the same way scripts/vm-env.sh does)")

    body = "\n".join(lines).encode()
    req = urllib.request.Request(
        f"{base.rstrip('/')}/write?precision=s",
        data=body,
        headers={"Authorization": f"Bearer {token}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        print(f"wrote {total} samples -- HTTP {resp.status}")


if __name__ == "__main__":
    main()
