#!/usr/bin/env bash
# Idempotently apply Cloud Billing budgets for this project.
# Rerunning updates existing budgets in place (matched by display name).
set -euo pipefail

# Account identifiers are read from the environment so they stay out of the repo.
# Set them before running, e.g. in a shell profile or an untracked local file:
#   BILLING_ACCOUNT=<billing-account-id>
#   BILLING_PROJECT=<project-id>
#   PROJECT_FILTER=projects/<project-number>
: "${BILLING_ACCOUNT:?set BILLING_ACCOUNT to the Cloud Billing account ID}"
: "${BILLING_PROJECT:?set BILLING_PROJECT to the project ID used for billing quota}"
: "${PROJECT_FILTER:?set PROJECT_FILTER to projects/<project-number>}"

# Service IDs (lookup: curl .../cloudbilling.googleapis.com/v1/services)
SVC_ARTIFACT_REGISTRY="services/149C-F9EC-3994"

find_budget_id() {
  gcloud billing budgets list \
    --billing-account="$BILLING_ACCOUNT" \
    --billing-project="$BILLING_PROJECT" \
    --filter="displayName=\"$1\"" \
    --format="value(name)" 2>/dev/null | head -n1 | awk -F/ '{print $NF}'
}

apply_budget() {
  local display_name="$1"
  local amount="$2"
  local services="$3"
  local thresholds="$4"  # space-separated percentages, e.g. "0.9 1.2"

  local -a threshold_create=()
  local -a threshold_update=(--clear-threshold-rules)
  for p in $thresholds; do
    threshold_create+=(--threshold-rule="percent=$p")
    threshold_update+=(--add-threshold-rule="percent=$p")
  done

  local existing
  existing=$(find_budget_id "$display_name")

  if [[ -n "$existing" ]]; then
    echo "==> Updating '$display_name' ($existing)"
    gcloud billing budgets update "$existing" \
      --billing-account="$BILLING_ACCOUNT" \
      --billing-project="$BILLING_PROJECT" \
      --display-name="$display_name" \
      --budget-amount="$amount" \
      --filter-projects="$PROJECT_FILTER" \
      --filter-services="$services" \
      "${threshold_update[@]}"
  else
    echo "==> Creating '$display_name'"
    gcloud billing budgets create \
      --billing-account="$BILLING_ACCOUNT" \
      --billing-project="$BILLING_PROJECT" \
      --display-name="$display_name" \
      --budget-amount="$amount" \
      --filter-projects="$PROJECT_FILTER" \
      --filter-services="$services" \
      "${threshold_create[@]}"
  fi
}

# Budget definitions below — add more apply_budget calls as new services need caps.
apply_budget \
  "Artifact Registry Monthly" \
  "40SEK" \
  "$SVC_ARTIFACT_REGISTRY" \
  "0.9 1.2"
