#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

for bin in curl jq; do
  command -v "$bin" >/dev/null 2>&1 || { echo "Missing required binary: $bin" >&2; exit 1; }
done

# Resolves VM_BASE_URL / VM_TOKEN from the environment or the repo's .env files.
source "$SCRIPT_DIR/vm-env.sh"

BASE_URL="$VM_BASE_URL"
AUTH_HEADER="Authorization: Bearer ${VM_TOKEN}"

PATTERN="${1:-}"

usage() {
  cat <<EOF
Usage: $(basename "$0") [pattern]

  pattern   Optional substring; only metrics whose name contains it are inspected.

Examples:
  $(basename "$0")                  # all metrics
  $(basename "$0") tibber           # only metrics containing "tibber"
EOF
  exit 1
}

[[ "${1:-}" == "-h" || "${1:-}" == "--help" ]] && usage

# Fetch all metric names.
METRICS_JSON="$(curl -sf "${BASE_URL}/api/v1/label/__name__/values" -H "$AUTH_HEADER")" \
  || { echo "Failed to list metrics from $BASE_URL" >&2; exit 1; }

if [[ -n "$PATTERN" ]]; then
  mapfile -t METRICS < <(jq -r --arg p "$PATTERN" '.data[] | select(contains($p))' <<<"$METRICS_JSON")
else
  mapfile -t METRICS < <(jq -r '.data[]' <<<"$METRICS_JSON")
fi

TOTAL=${#METRICS[@]}
echo "# Inspecting $TOTAL metric(s) from $BASE_URL${PATTERN:+ (filter: \"$PATTERN\")}" >&2

printf '| metric | labels (cardinality) | latest value | last seen |\n'
printf '|---|---|---|---|\n'

iso_from_epoch() {
  # Portable: macOS `date -r <epoch>`; GNU `date -d @<epoch>`.
  local epoch="$1"
  date -u -r "${epoch%.*}" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null \
    || date -u -d "@${epoch%.*}" +%Y-%m-%dT%H:%M:%SZ
}

urlencode_match() {
  # Produce the already-encoded value for `match[]={__name__="<metric>"}`.
  local metric="$1"
  printf 'match%%5B%%5D=%%7B__name__%%3D%%22%s%%22%%7D' "$metric"
}

i=0
for M in "${METRICS[@]}"; do
  i=$((i + 1))
  printf '# %d/%d %s\n' "$i" "$TOTAL" "$M" >&2

  match="$(urlencode_match "$M")"

  labels_json="$(curl -sf "${BASE_URL}/api/v1/labels?${match}" -H "$AUTH_HEADER")" || {
    printf '| %s | (error) | (error) | — |\n' "$M"
    continue
  }
  mapfile -t label_keys < <(jq -r '.data[] | select(. != "__name__")' <<<"$labels_json")

  label_parts=()
  for L in "${label_keys[@]}"; do
    lv_json="$(curl -sf "${BASE_URL}/api/v1/label/${L}/values?${match}" -H "$AUTH_HEADER")" || {
      label_parts+=("${L}(?)")
      continue
    }
    count="$(jq '.data | length' <<<"$lv_json")"
    label_parts+=("${L}(${count})")
  done
  labels_cell="$(IFS=', '; echo "${label_parts[*]:-—}")"

  q_json="$(curl -sf -G "${BASE_URL}/api/v1/query" \
    --data-urlencode "query=${M}" \
    -H "$AUTH_HEADER")" || {
    printf '| %s | %s | (error) | — |\n' "$M" "$labels_cell"
    continue
  }
  first="$(jq -c '.data.result[0] // empty' <<<"$q_json")"

  if [[ -z "$first" ]]; then
    q_json="$(curl -sf -G "${BASE_URL}/api/v1/query" \
      --data-urlencode "query=last_over_time(${M}[1d])" \
      -H "$AUTH_HEADER")" || q_json='{}'
    first="$(jq -c '.data.result[0] // empty' <<<"$q_json")"
  fi

  if [[ -z "$first" ]]; then
    printf '| %s | %s | (stale) | — |\n' "$M" "$labels_cell"
    continue
  fi

  ts="$(jq -r '.value[0]' <<<"$first")"
  val="$(jq -r '.value[1]' <<<"$first")"
  iso="$(iso_from_epoch "$ts")"

  printf '| %s | %s | %s | %s |\n' "$M" "$labels_cell" "$val" "$iso"
done
