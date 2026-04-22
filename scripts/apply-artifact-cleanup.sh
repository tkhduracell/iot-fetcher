#!/usr/bin/env bash
# Apply the Artifact Registry cleanup policy defined in artifact-registry-cleanup.json.
# Pass --no-dry-run as the first argument to activate deletions; default is dry-run (preview).
set -euo pipefail

PROJECT="filiplindqvist-com-ea66d"
LOCATION="europe"
REPO="images"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
POLICY_FILE="$SCRIPT_DIR/artifact-registry-cleanup.json"

MODE="${1:---dry-run}"
case "$MODE" in
  --dry-run|--no-dry-run) ;;
  *) echo "usage: $0 [--dry-run|--no-dry-run]" >&2; exit 2 ;;
esac

gcloud artifacts repositories set-cleanup-policies "$REPO" \
  --location="$LOCATION" \
  --project="$PROJECT" \
  --policy="$POLICY_FILE" \
  "$MODE"
