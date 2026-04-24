---
name: victoria-metrics-query
description: Use when running PromQL queries against VictoriaMetrics in this project — instant queries, range queries, listing metrics/labels/label-values, or exploring series with matchers. Wraps `scripts/vm-query.sh` which handles auth and endpoint resolution. Use this (or call the script directly) instead of hand-writing curl against `/api/v1/query`, `/api/v1/query_range`, `/api/v1/series`, or `/api/v1/label*`.
allowed-tools: Bash
---

# VictoriaMetrics Query

Query the VictoriaMetrics instance using `scripts/vm-query.sh`, which handles credential loading automatically.

## Arguments

- `query` (required): One of the following:
  - `metrics` - List all metric names
  - `labels` - List all label names
  - `label <name>` - List values for a specific label (e.g. `label source`)
  - Any other string - Treated as a PromQL instant query (e.g. `up`, `balboa_temperature{source="balboa"}`)
- `range` (optional): Set to `true` to use a range query instead of instant query
- `start` (optional): Start time for range queries (default: `1h` ago)
- `end` (optional): End time for range queries (default: `now`)
- `step` (optional): Step interval for range queries (default: `5m`)

## Steps

1. **Run the query** using the helper script:

   - **List metrics**: `./scripts/vm-query.sh metrics`
   - **List all label names**: `./scripts/vm-query.sh labels`
   - **List values for a label**: `./scripts/vm-query.sh label <name>`
   - **List series matching a matcher**: `./scripts/vm-query.sh series '<matcher>' [<matcher>...] [--start <t>] [--end <t>]`
   - **Instant PromQL query**: `./scripts/vm-query.sh query '<promql>' [--time <ts>] [--lookback <dur>]`
   - **Range PromQL query**: `./scripts/vm-query.sh range '<promql>' [--start <t>] [--end <t>] [--step <dur>] [--lookback <dur>]`

2. **Report findings**: Summarize the query results. For metric/label/series listings, show the count and values. For queries, format the results in a readable table.

## Notes

- For metric shape (label keys, cardinality, latest sample), prefer `scripts/vm-shape.sh [pattern]` — it's optimized for the "what does this metric look like" question.
- `--time` on `query` evaluates at a custom instant (future-dated plans, historical snapshots). Accepts RFC3339, unix epoch, or relative (`-1h`).
- `--lookback <dur>` overrides the default staleness window — useful for sparsely-sampled metrics.
- Never hand-write `curl` against the VM API — this script covers every endpoint we use.
