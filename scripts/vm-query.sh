#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# .env files are not committed, so in a git worktree they only exist in the main worktree root.
MAIN_PROJECT_DIR="$(git -C "$PROJECT_DIR" worktree list --porcelain | awk '/^worktree /{print $2; exit}')"
ENV_DIR="${MAIN_PROJECT_DIR:-$PROJECT_DIR}"

TOKEN=$(grep '^INFLUX_TOKEN=' "$ENV_DIR/fetcher-core/python/.env" | cut -d'=' -f2-)
DOMAIN=$(grep '^PROXY_DOMAIN=' "$ENV_DIR/https-proxy/.env" | cut -d'=' -f2-)
BASE_URL="https://${DOMAIN}"

usage() {
  cat <<EOF
Usage: $(basename "$0") [--format text|json] <command> [args...]

Commands:
  metrics                       List all metric names
  labels                        List all label names
  label <name>                  List values for a specific label
  series <match> [<match>...]   Return series matching one or more matchers
  query <promql>                Run an instant PromQL query
  range <promql>                Run a range PromQL query

Global options:
  --format text|json   Output format (default: text). Pass 'json' to pipe into jq/python.

Series options:
  --start <time>   Start time (e.g. -2h, 2024-01-01T00:00:00Z)
  --end <time>     End time

Query options (for 'query'):
  --time <ts>      Evaluate at a custom instant (RFC3339, unix epoch, or relative like -1h)
  --lookback <dur> lookback_delta parameter (e.g. 5m, 1h)

Range options:
  --start <time>   Start time (default: 1h ago)
  --end <time>     End time (default: now)
  --step <dur>     Step interval (default: 5m)
  --lookback <dur> lookback_delta parameter

Examples:
  $(basename "$0") metrics
  $(basename "$0") label source
  $(basename "$0") series 'pool_iqpump_plan_on{run="live"}'
  $(basename "$0") query 'up'
  $(basename "$0") query 'pool_iqpump_plan_on{run="live"}' --time \$((\$(date +%s)+86400))
  $(basename "$0") range 'rate(tibber_accumulatedConsumption[5m])' --start -2h --step 1m
  $(basename "$0") --format json query 'up' | jq '.data.result'
EOF
  exit 1
}

# Pre-parse --format so it can appear anywhere in the arg list.
FORMAT="text"
_new_args=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --format) FORMAT="$2"; shift 2 ;;
    *)        _new_args+=("$1"); shift ;;
  esac
done
if [[ ${#_new_args[@]} -gt 0 ]]; then
  set -- "${_new_args[@]}"
else
  set --
fi

[[ $# -lt 1 ]] && usage

format_list() {
  # For metrics/labels/label: plain newline-separated names in text mode; pretty JSON in json mode.
  if [[ "$FORMAT" == "json" ]]; then jq .; else jq -r '.data[]'; fi
}

format_result() {
  # For series/query/range: compact text by default; pretty JSON on --format json.
  if [[ "$FORMAT" == "json" ]]; then
    jq .
  else
    python3 -c "$(cat <<'PY'
import json, sys
from datetime import datetime, timezone

try:
    d = json.load(sys.stdin)
except Exception as e:
    sys.stderr.write(f"error parsing response: {e}\n")
    sys.exit(1)

if d.get("status") != "success":
    sys.stderr.write(json.dumps(d) + "\n")
    sys.exit(1)

def fmt_ts(ts):
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def fmt_labels(metric):
    name = metric.get("__name__", "")
    others = sorted((k, v) for k, v in metric.items() if k != "__name__")
    inner = ",".join(f'{k}="{v}"' for k, v in others)
    if name and inner: return f"{name}{{{inner}}}"
    if name:           return name
    return f"{{{inner}}}"

data = d.get("data")

# /api/v1/series -> list of label dicts
if isinstance(data, list):
    for s in data:
        print(fmt_labels(s))
    sys.exit(0)

rt = data.get("resultType")
result = data.get("result", [])

if rt == "vector":
    for r in result:
        v = r["value"][1]
        print(f"{v}\t{fmt_labels(r['metric'])}")
elif rt == "matrix":
    for i, r in enumerate(result):
        if i > 0: print()
        print(fmt_labels(r["metric"]))
        for ts, v in r["values"]:
            print(f"  {fmt_ts(ts)}  {v}")
elif rt in ("scalar", "string"):
    ts, v = result
    print(f"{v}\t{fmt_ts(ts)}")
else:
    print(json.dumps(d, indent=2))
PY
)"
  fi
}

CMD="$1"; shift

case "$CMD" in
  metrics)
    curl -sf "${BASE_URL}/api/v1/label/__name__/values" \
      -H "Authorization: Bearer ${TOKEN}" | format_list
    ;;
  labels)
    curl -sf "${BASE_URL}/api/v1/labels" \
      -H "Authorization: Bearer ${TOKEN}" | format_list
    ;;
  label)
    [[ $# -lt 1 ]] && { echo "Error: label name required" >&2; exit 1; }
    curl -sf "${BASE_URL}/api/v1/label/$1/values" \
      -H "Authorization: Bearer ${TOKEN}" | format_list
    ;;
  series)
    [[ $# -lt 1 ]] && { echo "Error: at least one matcher required" >&2; exit 1; }
    cargs=()
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --start) cargs+=(--data-urlencode "start=$2"); shift 2 ;;
        --end)   cargs+=(--data-urlencode "end=$2"); shift 2 ;;
        --*)     echo "Unknown option: $1" >&2; exit 1 ;;
        *)       cargs+=(--data-urlencode "match[]=$1"); shift ;;
      esac
    done
    curl -sfG "${BASE_URL}/api/v1/series" \
      "${cargs[@]}" \
      -H "Authorization: Bearer ${TOKEN}" | format_result
    ;;
  query)
    [[ $# -lt 1 ]] && { echo "Error: PromQL query required" >&2; exit 1; }
    QUERY="$1"; shift
    cargs=(--data-urlencode "query=${QUERY}")
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --time)     cargs+=(--data-urlencode "time=$2"); shift 2 ;;
        --lookback) cargs+=(--data-urlencode "lookback_delta=$2"); shift 2 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
      esac
    done
    curl -sf "${BASE_URL}/api/v1/query" \
      "${cargs[@]}" \
      -H "Authorization: Bearer ${TOKEN}" | format_result
    ;;
  range)
    [[ $# -lt 1 ]] && { echo "Error: PromQL query required" >&2; exit 1; }
    QUERY="$1"; shift
    START="$(date -u -v-1H +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%SZ)"
    END="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    STEP="5m"
    LOOKBACK=""
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --start)    START="$2"; shift 2 ;;
        --end)      END="$2"; shift 2 ;;
        --step)     STEP="$2"; shift 2 ;;
        --lookback) LOOKBACK="$2"; shift 2 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
      esac
    done
    cargs=(
      --data-urlencode "query=${QUERY}"
      --data-urlencode "start=${START}"
      --data-urlencode "end=${END}"
      --data-urlencode "step=${STEP}"
    )
    [[ -n "$LOOKBACK" ]] && cargs+=(--data-urlencode "lookback_delta=${LOOKBACK}")
    curl -sf "${BASE_URL}/api/v1/query_range" \
      "${cargs[@]}" \
      -H "Authorization: Bearer ${TOKEN}" | format_result
    ;;
  *)
    # Treat unknown command as a PromQL instant query for convenience.
    curl -sf "${BASE_URL}/api/v1/query" \
      --data-urlencode "query=$CMD" \
      -H "Authorization: Bearer ${TOKEN}" | format_result
    ;;
esac
