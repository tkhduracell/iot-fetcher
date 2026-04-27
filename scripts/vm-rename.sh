#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

MAIN_PROJECT_DIR="$(git -C "$PROJECT_DIR" worktree list --porcelain | awk '/^worktree /{print $2; exit}')"
ENV_DIR="${MAIN_PROJECT_DIR:-$PROJECT_DIR}"

TOKEN=$(grep '^INFLUX_TOKEN=' "$ENV_DIR/fetcher-core/python/.env" | cut -d'=' -f2-)
DOMAIN=$(grep '^PROXY_DOMAIN=' "$ENV_DIR/https-proxy/.env" | cut -d'=' -f2-)
BASE_URL="https://${DOMAIN}"

usage() {
  cat <<EOF
Usage: $(basename "$0") <old_name> <new_name> [--dry-run] [--delete]

Rename a VictoriaMetrics metric by exporting, transforming, and re-importing.

Options:
  --dry-run   Show a preview of transformed data without importing
  --delete    Delete the old metric after successful import

Examples:
  $(basename "$0") old_metric new_metric --dry-run
  $(basename "$0") old_metric new_metric
  $(basename "$0") old_metric new_metric --delete
EOF
  exit 1
}

[[ $# -lt 2 ]] && usage

OLD_NAME="$1"; shift
NEW_NAME="$1"; shift

DRY_RUN=false
DELETE=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=true; shift ;;
    --delete)  DELETE=true; shift ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

echo "Exporting metric: ${OLD_NAME}"
EXPORTED=$(curl -sf -G "${BASE_URL}/api/v1/export" \
  --data-urlencode "match[]={__name__=\"${OLD_NAME}\"}" \
  -H "Authorization: Bearer ${TOKEN}")

LINE_COUNT=$(echo "$EXPORTED" | grep -c '^' || true)

if [[ -z "$EXPORTED" || "$LINE_COUNT" -eq 0 ]]; then
  echo "Error: no data found for metric '${OLD_NAME}'" >&2
  exit 1
fi

echo "Exported ${LINE_COUNT} series"

TRANSFORMED=$(echo "$EXPORTED" | jq -c ".metric.__name__ = \"${NEW_NAME}\"")

if $DRY_RUN; then
  echo ""
  echo "=== Dry run preview ==="
  echo "Renaming: ${OLD_NAME} → ${NEW_NAME}"
  echo "Series count: ${LINE_COUNT}"
  echo ""
  echo "Sample (first 3 lines):"
  echo "$TRANSFORMED" | head -3 | jq .
  exit 0
fi

echo "Importing as: ${NEW_NAME}"
IMPORT_RESPONSE=$(echo "$TRANSFORMED" | curl -sf -X POST "${BASE_URL}/api/v1/import" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  --data-binary @- -w "%{http_code}" -o /dev/null)

if [[ "$IMPORT_RESPONSE" -ge 200 && "$IMPORT_RESPONSE" -lt 300 ]]; then
  echo "Import successful (HTTP ${IMPORT_RESPONSE})"
else
  echo "Error: import failed with HTTP ${IMPORT_RESPONSE}" >&2
  exit 1
fi

echo "Verifying new metric..."
VERIFY=$(curl -sf "${BASE_URL}/api/v1/query" \
  --data-urlencode "query=count({__name__=\"${NEW_NAME}\"})" \
  -H "Authorization: Bearer ${TOKEN}" | jq -r '.data.result[0].value[1] // "0"')
echo "Verification: ${VERIFY} series found for '${NEW_NAME}'"

echo ""
echo "Imported ${LINE_COUNT} series as '${NEW_NAME}'"

if $DELETE; then
  echo "Deleting old metric: ${OLD_NAME}"
  DELETE_RESPONSE=$(curl -sf -G -X POST "${BASE_URL}/api/v1/admin/tsdb/delete_series" \
    --data-urlencode "match[]={__name__=\"${OLD_NAME}\"}" \
    -H "Authorization: Bearer ${TOKEN}" -w "%{http_code}" -o /dev/null)
  if [[ "$DELETE_RESPONSE" -ge 200 && "$DELETE_RESPONSE" -lt 300 ]]; then
    echo "Deleted old metric '${OLD_NAME}' (HTTP ${DELETE_RESPONSE})"
  else
    echo "Warning: delete failed with HTTP ${DELETE_RESPONSE}" >&2
  fi
fi
