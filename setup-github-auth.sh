#!/bin/bash

# export GCLOUD_PROJECT=""
export GITHUB_REPO="tkhduracell/iot-fetcher"

GCLOUD_SERVICE_ACCOUNT=github-deployer-account
GCLOUD_SERVICE_ACCOUNT_EMAIL="${GCLOUD_SERVICE_ACCOUNT}@${GCLOUD_PROJECT}.iam.gserviceaccount.com"
GCLOUD_IDENTITY_POOL=github-deployer-auth-pool
GCLOUD_IDENTITY_PROVIDER=github-deployer-auth-provider

gcloud iam service-accounts create ${GCLOUD_SERVICE_ACCOUNT} --project "${GCLOUD_PROJECT}"

gcloud projects add-iam-policy-binding "${GCLOUD_PROJECT}" \
    --member="serviceAccount:${GCLOUD_SERVICE_ACCOUNT_EMAIL}" \
    --role="roles/artifactregistry.writer"
exit 0
gcloud services enable iamcredentials.googleapis.com --project "${GCLOUD_PROJECT}"

gcloud iam workload-identity-pools create ${GCLOUD_IDENTITY_POOL} --project="${GCLOUD_PROJECT}" --location="global" --display-name="GitHub Deploy Auth Pool"

GCLOUD_WORKLOAD_IDENTITY_POOL_ID=$(gcloud iam workload-identity-pools describe ${GCLOUD_IDENTITY_POOL} --project="${GCLOUD_PROJECT}" --location="global" --format="value(name)")

gcloud iam workload-identity-pools providers create-oidc ${GCLOUD_IDENTITY_PROVIDER} \
  --project="${GCLOUD_PROJECT}" \
  --location="global" \
  --workload-identity-pool="${GCLOUD_IDENTITY_POOL}" \
  --display-name="GitHub Deploy Auth Provider" \
  --attribute-mapping="google.subject=assertion.sub,attribute.actor=assertion.actor,attribute.repository=assertion.repository" \
  --issuer-uri="https://token.actions.githubusercontent.com"

gcloud iam service-accounts add-iam-policy-binding "${GCLOUD_SERVICE_ACCOUNT_EMAIL}" \
  --project="${GCLOUD_PROJECT}" \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/${GCLOUD_WORKLOAD_IDENTITY_POOL_ID}/attribute.repository/${GITHUB_REPO}"

WORKLOAD_IDENTITY_PROVIDER_ID=$(gcloud iam workload-identity-pools providers list --project="${GCLOUD_PROJECT}" --location="global" --workload-identity-pool="${GCLOUD_WORKLOAD_IDENTITY_POOL_ID}" --format="value(name)")

echo "WORKLOAD_IDENTITY_PROVIDER_ID: "
echo "${WORKLOAD_IDENTITY_PROVIDER_ID}"

echo "GCLOUD_SERVICE_ACCOUNT_EMAIL: "
echo "${GCLOUD_SERVICE_ACCOUNT_EMAIL}"