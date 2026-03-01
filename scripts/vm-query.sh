#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

TOKEN=$(grep '^INFLUX_TOKEN=' "$PROJECT_DIR/fetcher-core/python/.env" | cut -d'=' -f2-)
DOMAIN=$(grep '^PROXY_DOMAIN=' "$PROJECT_DIR/https-proxy/.env" | cut -d'=' -f2-)
BASE_URL="https://${DOMAIN}"

usage() {
  cat <<EOF
Usage: $(basename "$0") <command> [args...]

Commands:
  metrics                   List all metric names
  labels                    List all label names
  label <name>              List values for a specific label
  query <promql>            Run an instant PromQL query
  range <promql> [options]  Run a range PromQL query

Range options:
  --start <time>   Start time (default: 1h ago, e.g. -2h, 2024-01-01T00:00:00Z)
  --end <time>     End time (default: now)
  --step <dur>     Step interval (default: 5m)

Examples:
  $(basename "$0") metrics
  $(basename "$0") label source
  $(basename "$0") query 'up'
  $(basename "$0") query 'last_over_time(tibber_accumulatedCost[15m])'
  $(basename "$0") range 'rate(tibber_accumulatedConsumption[5m])' --start -2h --step 1m
EOF
  exit 1
}

[[ $# -lt 1 ]] && usage

CMD="$1"; shift

case "$CMD" in
  metrics)
    curl -sf "${BASE_URL}/api/v1/label/__name__/values" \
      -H "Authorization: Bearer ${TOKEN}" | jq -r '.data[]'
    ;;
  labels)
    curl -sf "${BASE_URL}/api/v1/labels" \
      -H "Authorization: Bearer ${TOKEN}" | jq -r '.data[]'
    ;;
  label)
    [[ $# -lt 1 ]] && { echo "Error: label name required" >&2; exit 1; }
    curl -sf "${BASE_URL}/api/v1/label/$1/values" \
      -H "Authorization: Bearer ${TOKEN}" | jq -r '.data[]'
    ;;
  query)
    [[ $# -lt 1 ]] && { echo "Error: PromQL query required" >&2; exit 1; }
    curl -sf "${BASE_URL}/api/v1/query" \
      --data-urlencode "query=$1" \
      -H "Authorization: Bearer ${TOKEN}" | jq .
    ;;
  range)
    [[ $# -lt 1 ]] && { echo "Error: PromQL query required" >&2; exit 1; }
    QUERY="$1"; shift
    START="$(date -u -v-1H +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%SZ)"
    END="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    STEP="5m"
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --start) START="$2"; shift 2 ;;
        --end)   END="$2"; shift 2 ;;
        --step)  STEP="$2"; shift 2 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
      esac
    done
    curl -sf "${BASE_URL}/api/v1/query_range" \
      --data-urlencode "query=${QUERY}" \
      --data-urlencode "start=${START}" \
      --data-urlencode "end=${END}" \
      --data-urlencode "step=${STEP}" \
      -H "Authorization: Bearer ${TOKEN}" | jq .
    ;;
  *)
    # Treat unknown command as a PromQL query for convenience
    curl -sf "${BASE_URL}/api/v1/query" \
      --data-urlencode "query=$CMD" \
      -H "Authorization: Bearer ${TOKEN}" | jq .
    ;;
esac
