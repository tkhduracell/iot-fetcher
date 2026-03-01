---
name: victoriametrics-query
description: Query VictoriaMetrics instance for metrics, labels, and PromQL queries
disable-model-invocation: true
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

   - **`metrics`**: `./scripts/vm-query.sh metrics`
   - **`labels`**: `./scripts/vm-query.sh labels`
   - **`label <name>`**: `./scripts/vm-query.sh label <name>`
   - **PromQL instant query**: `./scripts/vm-query.sh query '<promql>'`
   - **PromQL range query**: `./scripts/vm-query.sh range '<promql>' --start <start> --end <end> --step <step>`

2. **Report findings**: Summarize the query results. For metric/label listings, show the count and the values. For queries, format the results in a readable table.
