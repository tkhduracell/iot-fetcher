#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "$0")/.env"

gh secret set GRAFANA_URL --body "$GRAFANA_URL"
gh secret set GRAFANA_TOKEN --body "$GRAFANA_TOKEN"
gh secret set GRAFANA_FOLDER_UID --body "${GRAFANA_FOLDER_UID:-}"
