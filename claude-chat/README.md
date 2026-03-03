# Claude Chat — ai.filiplindqvist.com

Home automation AI assistant powered by the Claude Agent SDK with MCP tools for Sonos, Roborock, and VictoriaMetrics.

## Setup

### 1. Google OAuth credentials (gcloud CLI)

```bash
# Authenticate
gcloud auth login

# Set your project
gcloud config set project filiplindqvist-com-ea66d

# Create the OAuth consent screen (first time only)
# Go to: https://console.cloud.google.com/apis/credentials/consent
# Select "External", fill in app name "AI Assistant" and your email

# Create OAuth 2.0 Client ID
gcloud alpha iap oauth-clients create \
  --display_name="Claude Chat" \
  --type=web

# If the above doesn't work (alpha command availability varies),
# use the console directly:
#   1. Go to https://console.cloud.google.com/apis/credentials
#   2. Click "Create Credentials" → "OAuth client ID"
#   3. Application type: "Web application"
#   4. Name: "Claude Chat"
#   5. Authorized redirect URIs: https://ai.filiplindqvist.com/api/auth/callback/google
#   6. Copy the Client ID and Client Secret

# Or create via the REST API:
# Note: OAuth clients are not fully supported in gcloud CLI,
# so the most reliable path is the console or REST API.
```

The critical redirect URI is:
```
https://ai.filiplindqvist.com/api/auth/callback/google
```

### 2. Environment variables

```bash
cp .env.example .env
```

Fill in:

| Variable | How to get it |
|----------|--------------|
| `ANTHROPIC_API_KEY` | https://console.anthropic.com/settings/keys |
| `GOOGLE_CLIENT_ID` | From step 1 above |
| `GOOGLE_CLIENT_SECRET` | From step 1 above |
| `NEXTAUTH_SECRET` | `openssl rand -base64 32` |
| `NEXTAUTH_URL` | `https://ai.filiplindqvist.com` |
| `ALLOWED_EMAILS` | Comma-separated list of Google emails allowed to sign in |
| `INFLUXDB_V3_URL` | VictoriaMetrics host (without `https://`) |
| `INFLUXDB_V3_ACCESS_TOKEN` | VictoriaMetrics bearer token |

### 3. DNS

Point `ai.filiplindqvist.com` to the RPi5 public IP. Add `AI_DOMAIN=ai.filiplindqvist.com` to the https-proxy `.env`.

### 4. Build & deploy

```bash
# Build
make build

# Push to registry
make push

# Deploy on rpi5
sudo docker compose -f docker-compose.yml -f docker-compose.local.yml up -d
```

### 5. Local development

```bash
npm install
npm run dev  # http://localhost:3001
```
