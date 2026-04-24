---
name: victoria-metrics
description: Use when working with metrics in this iot-fetcher project — exploring what metrics exist, PromQL queries (instant/range/series), checking metric shape/cardinality, writing or debugging Grafana panels, writing metric-emitting code, diagnosing missing/stale data, or renaming metrics. Covers the `scripts/vm-shape.sh`, `scripts/vm-query.sh`, and `scripts/vm-rename.sh` helpers and the conventions for this deployment.
---

# VictoriaMetrics (iot-fetcher)

VictoriaMetrics runs on rpi5 (container name `database`, `-httpListenAddr=:8181`) and is exposed externally through a Caddy https-proxy that adds bearer-token auth. All three helper scripts live in `scripts/` and resolve credentials automatically — never hand-write `curl` against the VM API.

## Tooling cheat sheet

| Goal | Tool |
|---|---|
| What does this metric look like? (label keys, cardinality, latest sample, last-seen) | `scripts/vm-shape.sh [pattern]` |
| List metric names, labels, label values | `scripts/vm-query.sh metrics \| labels \| label <name>` |
| List series matching a PromQL selector | `scripts/vm-query.sh series '<matcher>' [<matcher>...]` |
| Run an instant PromQL query | `scripts/vm-query.sh query '<promql>'` |
| Run a range PromQL query | `scripts/vm-query.sh range '<promql>' --start -2h --step 1m` |
| Evaluate a query at a past/future instant | `scripts/vm-query.sh query '<promql>' --time <ts>` |
| Query sparsely-sampled metrics | `scripts/vm-query.sh query '<promql>' --lookback 1h` |
| Rename a metric | `scripts/vm-rename.sh <old> <new> [--delete]` (or invoke `victoria-metrics-rename` skill) |

**Default output is compact text** — one line per series/sample, ISO timestamps, PromQL-style `name{k="v"}` labels. Pass `--format json` when you need to pipe into `jq` or `python3`.

## When to reach for which tool

- Exploring an unfamiliar metric → start with `vm-shape.sh <pattern>` before querying. It tells you which labels exist and their cardinality, which shapes the query.
- Checking presence/absence → `vm-query.sh series '<matcher>'` is cheaper than a full `query_range` when all you need is "does this series exist".
- Instant sanity check on a live metric → `vm-query.sh query '<promql>'`.
- Plotting or analyzing over a window → `vm-query.sh range '<promql>' --start <t> --end <t> --step <dur>`.
- Evaluating at a specific historical moment (e.g. "what did the plan look like at 08:00 today") → `query '<promql>' --time <unix-ts-or-rfc3339>`.
- Sparsely-sampled metric where the default 5m staleness window misses points → add `--lookback <dur>`.

## Credentials

- `scripts/vm-query.sh` and `scripts/vm-rename.sh` read `INFLUX_TOKEN` from `fetcher-core/python/.env` and `PROXY_DOMAIN` from `https-proxy/.env` — no shell prep needed.
- `scripts/vm-shape.sh` reads `INFLUX_HOST` + `INFLUX_TOKEN` from `fetcher-core/python/.env.local`.
- All scripts are worktree-safe (they resolve the main repo root via `git rev-parse --git-common-dir`).

## Anti-patterns

- Don't hand-write `curl "$INFLUX_HOST/api/v1/query_range?query=..."` with `source .env.local`. Use `vm-query.sh range` — same call, 5× less typing, LLM-readable output.
- Don't pipe `vm-query.sh` output through `python3 -c "..."` to reformat timestamps or flatten the result. Default text format already has ISO timestamps and compact labels.
- Don't hit `/api/v1/series` directly when you only need a presence check — `vm-query.sh series` covers it.
- Don't re-auth via raw env for "one quick query". The scripts are already configured.

If you ever find an endpoint or parameter the scripts don't cover, extend `vm-query.sh` rather than falling back to curl — every past addition (`series`, `--time`, `--lookback`, `--format`) closed a curl-drift loophole.

## Conventions in this deployment

- Planner/backfill runs use `run="live"` vs `run="backfill"` labels to separate realtime writes from historical backfills. When querying for current behavior, filter `run="live"`.
- Plan metrics (`pool_iqpump_plan_*`) carry `plan_date`, `mode` (`optimal`/`fallback`), and `horizon` labels.
- Prefer `$__interval` with `spanNulls` in Grafana panels over hardcoded lookback windows.
- Edit Grafana dashboards via the TypeScript SDK + `convert_dashboard.py`, not raw JSON.

## Related skills

- `victoria-metrics-query` / `victoria-metrics-rename` — invokable skills that add a structured report-back or dry-run confirmation flow on top of the scripts. Use them when the user asks for a query or rename. For ad-hoc exploration mid-task, call the scripts directly.
- `health` — metric-freshness check (queries VM for staleness across the catalog).
- `grafana-update` — regenerate Grafana dashboard JSON from the TS SDK source.
