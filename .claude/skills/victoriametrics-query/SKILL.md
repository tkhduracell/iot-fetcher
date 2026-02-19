---
name: victoriametrics-query
description: Query VictoriaMetrics instance for metrics, labels, and PromQL queries
disable-model-invocation: true
allowed-tools: Bash
---

# VictoriaMetrics Query

Query the VictoriaMetrics instance using credentials from the project env files.

## Arguments

- `query` (required): One of the following:
  - `metrics` - List all metric names
  - `labels` - List all label names
  - `label <name>` - List values for a specific label (e.g. `label source`)
  - Any other string - Treated as a PromQL instant query (e.g. `up`, `balboa_temperature{source="balboa"}`)
- `range` (optional): Set to `true` to use a range query instead of instant query
- `start` (optional): Start time for range queries, relative or absolute (default: `1h` ago, e.g. `-2h`, `2024-01-01T00:00:00Z`)
- `end` (optional): End time for range queries (default: `now`)
- `step` (optional): Step interval for range queries (default: `5m`)

## Steps

1. **Extract credentials** from the project env files:

```bash
TOKEN=$(grep '^INFLUX_TOKEN=' fetcher-core/python/.env | cut -d'=' -f2-)
DOMAIN=$(grep '^PROXY_DOMAIN=' https-proxy/.env | cut -d'=' -f2-)
BASE_URL="https://${DOMAIN}"
```

2. **Execute the appropriate query** based on the `query` argument:

   - **`metrics`** - List all metric names:
     ```bash
     curl -s "${BASE_URL}/api/v1/label/__name__/values" -H "Authorization: Bearer ${TOKEN}" | jq .
     ```

   - **`labels`** - List all label names:
     ```bash
     curl -s "${BASE_URL}/api/v1/labels" -H "Authorization: Bearer ${TOKEN}" | jq .
     ```

   - **`label <name>`** - List values for a specific label:
     ```bash
     curl -s "${BASE_URL}/api/v1/label/<name>/values" -H "Authorization: Bearer ${TOKEN}" | jq .
     ```

   - **PromQL instant query** (any other string):
     ```bash
     curl -s "${BASE_URL}/api/v1/query" --data-urlencode "query=<query>" -H "Authorization: Bearer ${TOKEN}" | jq .
     ```

   - **PromQL range query** (when `range` is set):
     ```bash
     curl -s "${BASE_URL}/api/v1/query_range" --data-urlencode "query=<query>" --data-urlencode "start=<start>" --data-urlencode "end=<end>" --data-urlencode "step=<step>" -H "Authorization: Bearer ${TOKEN}" | jq .
     ```

3. **Report findings**: Summarize the query results. For metric/label listings, show the count and the values. For queries, format the results in a readable table.
